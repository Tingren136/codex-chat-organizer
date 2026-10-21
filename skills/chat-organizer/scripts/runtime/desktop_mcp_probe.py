"""Read-only MCP client for the unmodified, installed official desktop plugin.

Uses only its public MCP stdio transport and inherited desktop environment.
Never discovers/invents pipe addresses or invokes internal pipe methods.
"""
import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading

from batch_cache import ROOT, encoded

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from settings import desktop_server

READ_ONLY = {"list_projects", "list_threads", "read_thread"}


class DesktopMcp:
    def __init__(self, *, allow_project_move=False, allow_pin_restore=False, allow_title=False, allow_sidebar_organization=False):
        self.allow_project_move = allow_project_move
        self.allow_pin_restore = allow_pin_restore
        self.allow_title = allow_title
        self.allow_sidebar_organization = allow_sidebar_organization
        node = os.environ.get("CODEX_MCP_NODE_PATH")
        server = desktop_server()
        if not node or not Path(node).is_file() or not server.is_file():
            raise RuntimeError("Installed official runtime unavailable")
        if not os.environ.get("CODEX_APP_TOOLS_PIPE_PATH"):
            raise RuntimeError("Desktop did not supply its connection environment")
        self.p = subprocess.Popen([node, str(server)], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  text=True, encoding="utf-8",
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.q, self.seq = queue.Queue(), 0
        threading.Thread(target=self._read, daemon=True).start()
        self.request("initialize", {"protocolVersion": "2024-11-05",
                                    "capabilities": {},
                                    "clientInfo": {"name": "organizer-readonly-probe", "version": "0.1"}})
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _read(self):
        for line in self.p.stdout:
            try:
                self.q.put(json.loads(line))
            except ValueError:
                pass
        self.q.put({"eof": True})

    def send(self, obj):
        self.p.stdin.write(encoded(obj) + "\n")
        self.p.stdin.flush()

    def request(self, method, params):
        if method not in {"initialize", "tools/list", "tools/call"}:
            raise ValueError("Unsupported method")
        allowed = READ_ONLY | ({"move_thread_to_project"} if getattr(self, "allow_project_move", False) else set())
        if getattr(self, "allow_pin_restore", False):
            allowed |= {"move_thread_to_sidebar_section"}
        if getattr(self, "allow_title", False):
            allowed |= {"set_thread_title"}
        if getattr(self, 'allow_sidebar_organization', False):
            allowed |= {'create_sidebar_section', 'move_project_to_sidebar_section'}
        if method == "tools/call" and params.get("name") not in allowed:
            raise ValueError("Read-only probe refuses mutation")
        if method == "tools/call":
            # Forward the executor-provided identity of THIS chat, not a
            # target chat or a fabricated interaction/approval identity.
            current_thread = os.environ.get("CODEX_THREAD_ID")
            if not current_thread:
                raise RuntimeError("Executor thread context unavailable")
            params = {**params, "_meta": {"openai/threadId": current_thread}}
        self.seq += 1
        rid = self.seq
        self.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            msg = self.q.get(timeout=30)
            if msg.get("eof"):
                raise RuntimeError("MCP exited")
            if msg.get("id") == rid:
                if "error" in msg:
                    raise RuntimeError(encoded(msg["error"]))
                return msg["result"]
            if "id" in msg and "method" in msg:
                self.send({"jsonrpc": "2.0", "id": msg["id"],
                           "error": {"code": -32601, "message": "Read-only probe does not handle requests"}})

    def close(self):
        self.p.stdin.close()
        try:
            self.p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.p.terminate()
            self.p.wait(timeout=3)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["discover", "list_projects", "list_threads"])
    parser.add_argument("--arguments", default="{}")
    args = parser.parse_args()
    mcp = DesktopMcp()
    try:
        if args.action == "discover":
            value = mcp.request("tools/list", {})
            (ROOT / "work" / "desktop-mcp-tools.json").write_text(encoded(value), encoding="utf-8")
            print(encoded([t for t in value.get("tools", []) if t["name"] in
                           {"list_projects", "list_threads", "move_thread_to_project",
                            "move_thread_to_sidebar_section"}]))
        else:
            value = mcp.request("tools/call", {"name": args.action, "arguments": json.loads(args.arguments)})
            (ROOT / "work" / ("desktop-mcp-" + args.action + ".json")).write_text(encoded(value), encoding="utf-8")
            print(encoded(value))
    finally:
        mcp.close()


if __name__ == "__main__":
    main()
