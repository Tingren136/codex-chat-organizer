import tempfile
import unittest
from pathlib import Path
from batch_cache import Cache


class CacheTests(unittest.TestCase):
    def test_restart_metadata_delta_and_no_repeated_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.sqlite"
            cache = Cache(path)
            snapshot = {"complete": True, "meta": {"id": "test", "name": "old"},
                        "messages": [{"text": "first"}]}
            self.assertEqual(cache.ingest(snapshot)["status"], "new")
            batch = cache.export()
            self.assertEqual(len(batch["items"]), 1)
            self.assertEqual(cache.export()["items"], [])
            item = batch["items"][0]
            item.update(summary="first summary", title="title", reason="reason", project_id=None)
            cache.import_results(batch)
            with self.assertRaises(ValueError):
                cache.import_results(batch)
            cache.db.close()
            cache = Cache(path)
            snapshot["meta"]["name"] = "renamed"
            self.assertEqual(cache.ingest(snapshot)["status"], "skipped")
            self.assertEqual(cache.export()["items"], [])
            snapshot["messages"].append({"text": "new message"})
            cache.ingest(snapshot)
            delta = cache.export()["items"][0]
            self.assertEqual(delta["mode"], "delta")
            self.assertEqual(delta["messages"], [{"text": "new message"}])
            self.assertEqual(delta["previous_summary"], "first summary")
            cache.db.close()

    def test_incomplete_and_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Cache(Path(directory) / "cache.sqlite")
            with self.assertRaises(ValueError):
                cache.ingest({"complete": False})
            cache.ingest({"complete": True, "meta": {"id": "test"}, "messages": []})
            with cache.db:
                cache.db.execute("INSERT INTO locks VALUES('test','manual')")
            self.assertEqual(cache.export()["items"], [])
            cache.db.close()


if __name__ == "__main__":
    unittest.main()
