"""Bounded official App Server probe. No database access or model turns."""
import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from batch_cache import ROOT


class Rpc:
    def __init__(self, home=None):
        env = os.environ.copy()
        if home:
            env["CODEX_HOME"] = str(home)
        self.p = subprocess.Popen(
            [shutil.which("codex"), "app-server", "--stdio"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.q = queue.Queue()
        self.seq = 0
        threading.Thread(target=self._read, daemon=True).start()
        self.call("initialize", {
            "clientInfo": {"name": "conversation_organizer_probe", "version": "0.1.0"},
            "capabilities": {"experimentalApi": True}})
        self.send({"method": "initialized"})

    def _read(self):
        for line in self.p.stdout:
            try:
                self.q.put(json.loads(line))
            except json.JSONDecodeError:
                pass
        self.q.put({"eof": True})

    def send(self, obj):
        self.p.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.p.stdin.flush()

    def call(self, method, params, timeout=45):
        self.seq += 1
        rid = self.seq
        self.send({"id": rid, "method": method, "params": params})
        end = time.monotonic() + timeout
        while True:
            msg = self.q.get(timeout=max(0.01, end - time.monotonic()))
            if msg.get("eof"):
                raise RuntimeError("App Server exited")
            if msg.get("id") == rid:
                if "error" in msg:
                    raise RuntimeError(json.dumps(msg["error"], ensure_ascii=False))
                return msg["result"]
            if "id" in msg and "method" in msg:
                self.send({"id": msg["id"], "error": {
                    "code": -32601, "message": "Probe does not execute server requests"}})

    def close(self):
        self.p.stdin.close()
        try:
            self.p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.p.terminate()
            self.p.wait(timeout=5)


def save(name, data):
    target = ROOT / "work" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["inventory", "read"])
    parser.add_argument("--thread-id")
    args = parser.parse_args()
    rpc = Rpc()
    try:
        if args.action == "inventory":
            result = {}
            for method, params in [
                ("project/list", {"limit": 50}),
                ("thread/list", {"limit": 12, "sortKey": "updated_at",
                                 "useStateDbOnly": True})]:
                try:
                    result[method] = rpc.call(method, params)
                except Exception as exc:
                    result[method] = {"error": str(exc)}
            save("inventory.json", result)
            for method, value in result.items():
                print(method, json.dumps(
                    [{k: row.get(k) for k in ("id", "name", "projectId")}
                     for row in value.get("data", [])] if "data" in value else value,
                    ensure_ascii=False))
        else:
            if not args.thread_id:
                parser.error("--thread-id required")
            meta = rpc.call("thread/read", {"threadId": args.thread_id, "includeTurns": False})
            turns, cursor, seen = [], None, set()
            for page in range(100):
                params = {"threadId": args.thread_id, "limit": 20,
                          "sortDirection": "asc", "itemsView": "full"}
                if cursor:
                    params["cursor"] = cursor
                data = rpc.call("thread/turns/list", params)
                if any(t.get("itemsView") not in (None, "full")
                       for t in data.get("data", [])):
                    raise RuntimeError("Server returned partial item view")
                turns.extend(data.get("data", []))
                cursor = data.get("nextCursor")
                if not cursor:
                    break
                if cursor in seen:
                    raise RuntimeError("Repeated pagination cursor")
                seen.add(cursor)
            else:
                raise RuntimeError("Read cap reached; not complete")
            ids = [t.get("id") for t in turns]
            if len(set(ids)) != len(ids):
                raise RuntimeError("Duplicate turn IDs; history may have changed during pagination")
            # Persist only visible user/assistant text, not tools or hidden reasoning.
            visible = []
            for turn in turns:
                for item in turn.get("items", []):
                    if item.get("type") in ("userMessage", "agentMessage"):
                        visible.append({"turn": turn.get("id"), "type": item["type"],
                                        "text": item.get("text"), "content": item.get("content")})
            thread = meta["thread"]
            result = {"meta": {k: thread.get(k) for k in (
                "id", "name", "preview", "cwd", "projectId", "source")},
                "turn_count": len(turns), "complete": not cursor, "messages": visible}
            save(f"sample-{args.thread_id}.json", result)
            print(json.dumps({"id": args.thread_id, "name": thread.get("name"), "turn_count": len(turns),
                              "messages": len(visible), "complete": not cursor}, ensure_ascii=False))
    finally:
        rpc.close()


if __name__ == "__main__":
    main()
