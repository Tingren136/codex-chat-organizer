import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from batch_cache import Cache, encoded
import oneclick as app


class FakeClient:
    instances = 0
    writes = 0
    def __init__(self, **kwargs):
        FakeClient.instances += 1
        self.threads = {i: {'id': i, 'title': i, 'kind': 'codex', 'hostId': 'local',
                           'projectId': None, 'cwd': 'same', 'status': 'idle'} for i in ('new1', 'new2', 'old')}
    def request(self, method, params):
        name, args = params['name'], params['arguments']
        if name == 'list_projects':
            data = {'projects': [{'projectId': 'dest', 'label': 'Dest', 'projectKind': 'local', 'hostId': 'local'}]}
        elif name == 'list_threads':
            data = {'pinnedThreads': [], 'threads': list(self.threads.values())}
        elif name == 'set_thread_title':
            FakeClient.writes += 1
            self.threads[args['threadId']]['title'] = args['title']
            data = {'ok': True}
        elif name == 'move_thread_to_project':
            FakeClient.writes += 1
            self.threads[args['threadId']]['projectId'] = args['projectId']
            data = {'ok': True}
        else:
            raise AssertionError(name)
        return {'content': [{'type': 'text', 'text': encoded(data)}]}
    def close(self):
        pass


class FakeRpc:
    def close(self):
        pass


class FlowTests(unittest.TestCase):
    def test_single_batch_complete_then_zero_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'work').mkdir()
            db = root/'cache.db'
            c = Cache(db)
            with c.db:
                c.db.execute("INSERT INTO locks VALUES('old','protected')")
            c.db.close()
            calls = []
            def snapshot(rpc, tid):
                self.assertNotEqual(tid, 'old')
                return {'meta': {'id': tid, 'name': tid, 'cwd': 'same'},
                        'messages': [{'text': tid}], 'complete': True}
            def model(cache, batch_id):
                calls.append(batch_id)
                batch = json.loads((root/'work'/('batch-'+batch_id+'.json')).read_text(encoding='utf-8'))
                for item in batch['items']:
                    item.update(summary='summary', title=item['thread']+' renamed', reason='reason',
                                project_id='dest', title_action='rename')
                cache.import_results(batch)
                output = root/'result.json'
                output.write_text(encoded(batch), encoding='utf-8')
                return {'state': 'completed', 'output': str(output)}
            FakeClient.instances = 0
            FakeClient.writes = 0
            with patch.multiple(app, ROOT=root, DB=db, DesktopMcp=FakeClient,
                                Rpc=FakeRpc, read_snapshot=snapshot, analyze=model):
                result = app.run('test', ['old', 'new1', 'new2'])
                self.assertEqual(result['state'], 'completed')
                self.assertEqual(len(result['items']), 2)
                self.assertTrue(result['protected_unchanged']['old'])
                self.assertTrue(app.run('test')['skipped_existing_run'])
                self.assertEqual(app.run('next', ['new1', 'new2'])['state'], 'no_new_work')
                self.assertEqual(len(calls), 1)
                self.assertEqual(FakeClient.instances, 1)
                self.assertEqual(FakeClient.writes, 0)
                self.assertTrue(all(r['state'] == 'awaiting_title_review' for r in result['items']))


if __name__ == '__main__':
    unittest.main()
