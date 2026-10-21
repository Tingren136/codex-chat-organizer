import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from batch_cache import Cache
from project_executor import execute, inventory


class FakeDesktop:
    writes = 0
    fail = False
    pinned = False
    def __init__(self, **kwargs):
        self.thread = {"id": "fresh", "title": "Title", "projectId": None,
                       "kind": "codex", "hostId": "local", "cwd": "same", "status": "idle"}
        if self.pinned:
            self.thread["pinnedIndex"] = 2
    def request(self, method, params):
        name = params["name"]
        if name == "list_projects":
            data = {"projects": [{"projectId": "dest", "label": "Dest", "projectKind": "local"}]}
        elif name == "list_threads":
            data = {"pinnedThreads": [], "threads": [self.thread]}
        elif name == "move_thread_to_project":
            FakeDesktop.writes += 1
            if self.fail:
                return {"isError": True, "content": [{"type": "text", "text": "folder consent required"}]}
            self.thread["projectId"] = "dest"
            self.thread.pop("pinnedIndex", None)
            data = {"ok": True}
        elif name == "move_thread_to_sidebar_section":
            self.thread["pinnedIndex"] = 2
            data = {"ok": True}
        else:
            raise AssertionError(name)
        return {"content": [{"type": "text", "text": json.dumps(data)}]}
    def close(self):
        pass


PLAN = {"id": "test", "items": [{"threadId": "fresh", "projectId": "dest",
                               "expectedTitle": "Title", "expectedProjectId": None}]}


class ExecutorTests(unittest.TestCase):
    def test_older_thread_outside_list_window_is_read_only(self):
        class OlderClient:
            def request(self, method, params):
                if params['name'] == 'list_threads':
                    data = {'pinnedThreads': [], 'threads': []}
                elif params['name'] == 'read_thread':
                    data = {'thread': {'id': 'older', 'kind': 'codex', 'hostId': 'local',
                        'title': 'Older', 'cwd': r'C:\\folder', 'status': {'type':'notLoaded'}}}
                else:
                    raise AssertionError('No mutations allowed')
                return {'content': [{'type':'text','text':json.dumps(data)}]}
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'.codex-global-state.json').write_text(json.dumps({
                'thread-project-assignments': {'older': {'projectId':'real-project'}}}), encoding='utf-8')
            with patch.dict('os.environ', {'CODEX_HOME':d}):
                old = inventory(OlderClient(), ['older'])['older']
                self.assertEqual(old['projectId'], 'real-project')
                self.assertEqual(old['status'], 'notLoaded')
                self.assertEqual(old['cwd'], r'C:\folder')

    def test_success_or_permission_failure_never_repeated(self):
        for fail in [False, True]:
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as d:
                path = Path(d) / "test.db"
                Cache(path).db.close()
                FakeDesktop.writes, FakeDesktop.fail = 0, fail
                FakeDesktop.pinned = False
                result = execute(PLAN, path, FakeDesktop)
                self.assertEqual(result[0]["desktop_state_verified"], not fail)
                self.assertEqual(execute(PLAN, path, FakeDesktop)[0]["status"], "skipped_processed")
                self.assertEqual(FakeDesktop.writes, 1)

    def test_native_move_unpins_and_adapter_restores(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "test.db"
            Cache(path).db.close()
            FakeDesktop.writes, FakeDesktop.fail, FakeDesktop.pinned = 0, False, True
            try:
                result = execute(PLAN, path, FakeDesktop)
                self.assertTrue(result[0]["desktop_state_verified"])
                self.assertTrue(result[0]["pin_restoration"]["verified"])
                self.assertEqual(result[0]["after"]["pinnedIndex"], 2)
            finally:
                FakeDesktop.pinned = False

    def test_processed_chat_never_starts_client(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "test.db"
            cache = Cache(path)
            with cache.db:
                cache.db.execute("INSERT INTO locks VALUES('fresh','protected')")
            cache.db.close()
            def forbidden(**kwargs):
                raise AssertionError("Should not connect")
            self.assertEqual(execute(PLAN, path, forbidden)[0]["status"], "skipped_processed")


if __name__ == "__main__":
    unittest.main()
