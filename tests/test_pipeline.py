import tempfile
import unittest
from pathlib import Path
from batch_cache import Cache
from pipeline import execute_titles, initialize


class FakeRpc:
    writes = 0
    fail = False
    def call(self, method, params):
        if method == "thread/read":
            return {"thread": {"id": "new", "name": "new title" if self.writes else "old",
                               "cwd": "test", "updatedAt": 10}}
        if method == "thread/turns/list":
            return {"data": [], "nextCursor": None}
        if method == "thread/name/set":
            FakeRpc.writes += 1
            if self.fail:
                raise RuntimeError("Timeout after dispatch")
            return {}
        raise AssertionError("Unexpected method: " + method)

    def close(self):
        pass


class PipelineTests(unittest.TestCase):
    def setup_batch(self, path):
        c = Cache(path)
        initialize(c)
        c.ingest({"complete": True, "meta": {"id": "new", "name": "old", "cwd": "test"},
                  "messages": []})
        batch = c.export()
        batch["items"][0].update(summary="summary", title="new title", reason="reason",
                                 project_id=None, title_action="rename")
        c.import_results(batch)
        return c, batch["batch"]

    def test_success_restart_never_writes_twice(self):
        FakeRpc.writes, FakeRpc.fail = 0, False
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "cache.sqlite"
            c, batch = self.setup_batch(path)
            receipt = execute_titles(c, batch, FakeRpc)
            self.assertTrue(receipt[0]["api_verified"])
            self.assertFalse(receipt[0]["ui_verified"])
            c.db.close()
            c = Cache(path)
            self.assertTrue(execute_titles(c, batch, FakeRpc)[0]["skipped"])
            self.assertEqual(FakeRpc.writes, 1)
            c.db.close()

    def test_uncertain_write_never_retries(self):
        FakeRpc.writes, FakeRpc.fail = 0, True
        with tempfile.TemporaryDirectory() as d:
            c, batch = self.setup_batch(Path(d) / "cache.sqlite")
            execute_titles(c, batch, FakeRpc)
            self.assertTrue(execute_titles(c, batch, FakeRpc)[0]["skipped"])
            self.assertEqual(FakeRpc.writes, 1)
            c.db.close()


if __name__ == "__main__":
    unittest.main()
