import copy
import sqlite3
import unittest
from unittest.mock import patch

import resume_groups as finish


class FakeClient:
    def __init__(self, items):
        self.items = copy.deepcopy(items)
        self.calls = []
        self.fail = False

    def request(self, method, params):
        self.calls.append(params['arguments'])
        if not self.fail:
            self.items[params['arguments']['threadId']]['projectId'] = params['arguments']['projectId']
        return {'isError': self.fail}


class PendingTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.before = {'t': {'title': '待处理', 'cwd': 'D:\\旧目录', 'projectId': None,
                              'pinnedIndex': None, 'updatedAt': 1, 'status': 'idle'},
                       'done': {'title': '不许再改', 'projectId': 'old'}}
        self.client = FakeClient(self.before)
        self.plan = {'id': 'repair', 'pending': [{'thread': 't', 'target': 'new',
                                                'expected': self.before['t']}]}

    def run_moves(self, projects=None):
        with patch.object(finish, 'inventory', side_effect=lambda client, ids: copy.deepcopy(client.items)):
            return finish.move_pending(self.plan, self.client, self.db, projects or {'new'})

    def tearDown(self):
        self.db.close()

    def test_once_only_moves_pending_and_preserves_finished(self):
        self.run_moves()
        self.run_moves()
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(self.client.items['done'], self.before['done'])
        self.assertEqual(self.client.items['t']['cwd'], self.before['t']['cwd'])

    def test_missing_desktop_project_never_attempts_move(self):
        with self.assertRaises(ValueError):
            self.run_moves({'other'})
        self.assertEqual(self.client.calls, [])

    def test_source_failure_is_not_retried(self):
        self.client.fail = True
        first = self.run_moves()
        second = self.run_moves()
        self.assertEqual(first[0]['state'], 'needs_review')
        self.assertEqual(len(self.client.calls), 1)
        self.assertTrue(second[0]['skipped_existing_attempt'])

    def test_manual_title_change_prevents_move(self):
        self.client.items['t']['title'] = '用户新改标题'
        with self.assertRaises(ValueError):
            self.run_moves()
        self.assertEqual(self.client.calls, [])

    def test_already_at_target_does_not_mutate(self):
        self.client.items['t']['projectId'] = 'new'
        result = self.run_moves()
        self.assertEqual(result[0]['state'], 'already_at_target')
        self.assertEqual(self.client.calls, [])


if __name__ == '__main__':
    unittest.main()
