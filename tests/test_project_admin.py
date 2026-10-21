from pathlib import Path
import tempfile
import unittest

from project_admin import ensure_project


class FakeRpc:
    def __init__(self):
        self.projects = []
        self.creates = 0
        self.fail = False

    def call(self, method, params):
        if method == 'project/list':
            return {'data': self.projects, 'nextCursor': None}
        if method == 'project/read':
            return {'project': next(p for p in self.projects if p['id'] == params['projectId'])}
        if method == 'project/create':
            self.creates += 1
            if self.fail:
                raise TimeoutError('uncertain request')
            project = {'id': 'project-demo', 'name': params['name'], 'roots': params['roots'],
                       'createdAt': 1, 'updatedAt': 1, 'metadata': {}}
            self.projects.append(project)
            return {'project': project}
        raise AssertionError(method)


class ProjectCreateTests(unittest.TestCase):
    def test_create_then_replay_reuses_same_id(self):
        with tempfile.TemporaryDirectory() as folder:
            rpc = FakeRpc()
            journal = Path(folder) / 'journal.json'
            first = ensure_project(rpc, '示例项目', [folder], journal, 'stable-key')
            second = ensure_project(rpc, '示例项目', [folder], journal, 'stable-key')
            self.assertEqual(first['id'], second['id'])
            self.assertEqual(rpc.creates, 1)

    def test_timeout_never_blindly_retries_create(self):
        with tempfile.TemporaryDirectory() as folder:
            rpc = FakeRpc(); rpc.fail = True
            journal = Path(folder) / 'journal.json'
            with self.assertRaises(TimeoutError):
                ensure_project(rpc, '示例项目', [folder], journal, 'key')
            with self.assertRaises(RuntimeError):
                ensure_project(rpc, '示例项目', [folder], journal, 'key')
            self.assertEqual(rpc.creates, 1)

    def test_same_name_different_root_requires_decision(self):
        with tempfile.TemporaryDirectory() as folder:
            rpc = FakeRpc()
            rpc.projects = [{'id': 'existing', 'name': '示例项目', 'roots': [{'path': 'D:\\different'}]}]
            with self.assertRaises(ValueError):
                ensure_project(rpc, '示例项目', [folder], Path(folder) / 'journal.json', 'key')
            self.assertEqual(rpc.creates, 0)

    def test_existing_matching_project_is_not_created(self):
        with tempfile.TemporaryDirectory() as folder:
            rpc = FakeRpc()
            rpc.projects = [{'id': 'existing', 'name': '示例项目', 'roots': [{'path': folder}]}]
            self.assertEqual(ensure_project(rpc, '示例项目', [folder], Path(folder) / 'journal.json', 'key')['id'], 'existing')
            self.assertEqual(rpc.creates, 0)


if __name__ == '__main__':
    unittest.main()
