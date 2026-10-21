"""Bounded, once-only official desktop project moves; no model/UI automation."""
import argparse
import json
import os
import ntpath
from pathlib import Path
from settings import codex_home
import sqlite3
from batch_cache import ROOT, encoded
from desktop_mcp_probe import DesktopMcp

DB = ROOT / "work" / "organizer-cache.sqlite"


def unpack(response):
    if response.get("isError"):
        raise RuntimeError(encoded(response.get("content", [])))
    texts = [c["text"] for c in response.get("content", []) if c.get("type") == "text"]
    if len(texts) != 1:
        raise ValueError("Unexpected MCP result")
    return json.loads(texts[0])


def call(client, name, arguments):
    return unpack(client.request("tools/call", {"name": name, "arguments": arguments}))


def inventory(client, extra_ids=()):
    data = call(client, "list_threads", {"limit": 50})
    found = {t["id"]: t for t in data["pinnedThreads"] + data["threads"]}
    missing = [tid for tid in extra_ids if tid not in found]
    if missing:
        # Native list has a mixed-source 50-row window. Older local chats can
        # be inspected by id; project membership is read, never written, from
        # the same desktop state used by the sidebar.
        home = codex_home()
        desktop = json.loads((home/'.codex-global-state.json').read_text(encoding='utf-8'))
        for tid in missing:
            raw = call(client, 'read_thread', {'threadId': tid, 'hostId': 'local',
                       'turnLimit': 1, 'includeOutputs': False, 'maxOutputCharsPerItem': 1})
            t = raw['thread']
            if t['id'] != tid or t.get('kind') != 'codex' or t.get('hostId') != 'local':
                raise ValueError('Unexpected older thread identity')
            assignment = desktop.get('thread-project-assignments', {}).get(tid)
            t['projectId'] = assignment.get('projectId') if assignment else None
            if isinstance(t.get('status'), dict):
                t['status'] = t['status'].get('type')
            if t.get('cwd'):
                t['cwd'] = ntpath.normpath(t['cwd'])
            t.pop('preview', None)
            found[tid] = t
    return found


def restore_pin(db, client, tid, before, original_inventory):
    """Compensate only this newly moved thread's lost pin, exactly once."""
    index = before.get("pinnedIndex")
    if index is None:
        return None
    now = inventory(client)
    if now.get(tid, {}).get("pinnedIndex") == index:
        return None
    db.execute("""CREATE TABLE IF NOT EXISTS pin_restorations(
        thread TEXT PRIMARY KEY, state TEXT, receipt TEXT)""")
    if db.execute("SELECT 1 FROM pin_restorations WHERE thread=?", (tid,)).fetchone():
        raise RuntimeError("Pin restoration already attempted; do not repeat")
    following = sorted((t for t in original_inventory.values()
                        if t.get("pinnedIndex", 0) > index),
                       key=lambda t: t["pinnedIndex"])
    args = {"threadId": tid, "hostId": "local", "source": "codex", "sectionId": "pinned"}
    if following:
        args["beforeThreadId"] = following[0]["id"]
    with db:
        db.execute("INSERT INTO pin_restorations VALUES(?,'attempted',NULL)", (tid,))
    response = client.request("tools/call", {"name": "move_thread_to_sidebar_section", "arguments": args})
    after = inventory(client).get(tid)
    verified = after and after.get("pinnedIndex") == index
    receipt = {"response": response, "verified": bool(verified), "after": after}
    with db:
        db.execute("UPDATE pin_restorations SET state=?,receipt=? WHERE thread=?",
                   ("verified" if verified else "needs_review", encoded(receipt), tid))
    if not verified:
        raise RuntimeError("Pin restoration needs review")
    return receipt


def execute(plan, db_path=DB, factory=DesktopMcp):
    if not 1 <= len(plan["items"]) <= 2:
        raise ValueError("Only one or two explicitly selected new samples allowed")
    if len({i["threadId"] for i in plan["items"]}) != len(plan["items"]):
        raise ValueError("Duplicate targets")
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS project_moves(
        thread TEXT PRIMARY KEY, plan TEXT NOT NULL, state TEXT NOT NULL,
        before_json TEXT NOT NULL, target TEXT NOT NULL, receipt TEXT)""")
    client = None
    reports = []
    try:
        eligible = []
        for item in plan["items"]:
            tid = item["threadId"]
            if (tid == os.environ.get("CODEX_THREAD_ID") or
                db.execute("SELECT 1 FROM locks WHERE thread=?", (tid,)).fetchone() or
                db.execute("SELECT 1 FROM snapshots WHERE thread=?", (tid,)).fetchone() or
                db.execute("SELECT 1 FROM project_moves WHERE thread=?", (tid,)).fetchone()):
                reports.append({"thread": tid, "status": "skipped_processed"})
            else:
                eligible.append(item)
        if not eligible:
            return reports
        client = factory(allow_project_move=True, allow_pin_restore=True)
        projects = {p["projectId"]: p for p in call(client, "list_projects", {})["projects"]}
        initial = inventory(client)
        locked_ids = {r[0] for r in db.execute("SELECT thread FROM locks")}
        protected = {tid: initial[tid] for tid in locked_ids if tid in initial}
        for item in eligible:
            tid, target = item["threadId"], item["projectId"]
            current = inventory(client).get(tid)
            if not current or target not in projects:
                raise ValueError("Target/thread absent from current desktop inventory")
            if current.get("kind") != "codex" or current.get("hostId") != "local":
                raise ValueError("This adapter currently only moves local Codex chats")
            if projects[target].get("projectKind") != "local":
                raise ValueError("Cross-backend consent flow is not supported here")
            if current.get("status") == "active":
                raise ValueError("Active chat excluded")
            if current["title"] != item["expectedTitle"] or current.get("projectId") != item["expectedProjectId"]:
                raise ValueError("Concurrent change; do not overwrite")
            if current.get("projectId") == target:
                reports.append({"thread": tid, "status": "already_at_target"})
                continue
            # Write intent + lock atomically before any mutation, including failures.
            with db:
                db.execute("INSERT INTO project_moves VALUES(?,?,'attempted',?,?,NULL)",
                           (tid, plan["id"], encoded(current), target))
                db.execute("INSERT INTO locks VALUES(?,?)",
                           (tid, "项目归类已尝试；禁止自动重试、重新分析或改名"))
            receipt = {"thread": tid, "title": current["title"], "target": target,
                       "target_label": projects[target]["label"],
                       "model_calls": 0, "screen_control": False, "visual_verified": False}
            try:
                raw = client.request("tools/call", {"name": "move_thread_to_project",
                                     "arguments": {"threadId": tid, "projectId": target,
                                                   "hostId": "local", "source": "codex"}})
                receipt["tool_response"] = raw
                after = inventory(client).get(tid)
                if after and after.get("projectId") == target:
                    receipt["pin_restoration"] = restore_pin(db, client, tid, current, initial)
                    after = inventory(client).get(tid)
                receipt["after"] = after
                verified = bool(after and after.get("projectId") == target
                                and after["title"] == current["title"]
                                and after.get("cwd") == current.get("cwd")
                                and after.get("pinnedIndex") == current.get("pinnedIndex"))
                receipt["desktop_state_verified"] = verified
                state = "desktop_state_verified" if verified else "needs_review"
                # An error with a completed side effect is retained honestly.
                receipt["tool_reported_error"] = bool(raw.get("isError"))
            except Exception as exc:
                state = "needs_review"
                receipt["error"] = str(exc)
            receipt["status"] = state
            with db:
                db.execute("UPDATE project_moves SET state=?,receipt=? WHERE thread=?",
                           (state, encoded(receipt), tid))
            reports.append(receipt)
            if state == "needs_review":
                break
        final = inventory(client)
        for tid, before in protected.items():
            after = final.get(tid)
            if not after or any(before.get(k) != after.get(k) for k in
                                ("title", "projectId", "cwd", "pinnedIndex")):
                raise RuntimeError("Protected item changed externally; review, no restoration")
        return reports
    finally:
        if client:
            client.close()
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8-sig"))
    reports = execute(plan)
    path = ROOT / "work" / ("project-receipt-" + plan["id"] + ".json")
    # Preserve the first execution evidence when duplicate runs only skip.
    if not path.exists():
        path.write_text(encoded(reports), encoding="utf-8")
    print(encoded(reports))


if __name__ == "__main__":
    main()
