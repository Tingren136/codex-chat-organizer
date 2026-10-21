import copy
import json
from pathlib import Path
import tempfile
import unittest

import offline_registration as repair


class RegistrationSafetyTests(unittest.TestCase):
    def setUp(self):
        self.state = {'local-projects': {'old': {'id': 'old', 'name': '保留'}},
                      'project-order': ['old'], 'projectless-thread-ids': ['other'],
                      'thread-project-assignments': {'done': {'projectId': 'old'}},
                      'sidebar': {'collapsed': ['keep']}, 'unknown': [1, 2]}
        self.plan = {'projects': [{'id': 'new', 'name': '新项目', 'rootPaths': ['D:\\项目'],
                                  'createdAt': 1, 'updatedAt': 1}],
                     'orphan': 'orphan', 'expected_assignment': None,
                     'expected_projectless': False}

    def test_adds_only_missing_project_and_orphan_marker(self):
        before = copy.deepcopy(self.state)
        result = repair.patched_state(self.state, self.plan)
        self.assertEqual(self.state, before)
        self.assertEqual(result['local-projects']['old'], before['local-projects']['old'])
        self.assertEqual(result['project-order'], ['old', 'new'])
        self.assertEqual(result['projectless-thread-ids'], ['other', 'orphan'])
        for key in ['thread-project-assignments', 'sidebar', 'unknown']:
            self.assertEqual(result[key], before[key])

    def test_second_apply_is_noop(self):
        result = repair.patched_state(self.state, self.plan)
        self.assertEqual(repair.patched_state(result, self.plan), result)

    def test_repairs_both_proven_orphans_in_one_exit(self):
        self.plan['orphans'] = ['orphan', 'second-orphan']
        result = repair.patched_state(self.state, self.plan)
        self.assertEqual(result['projectless-thread-ids'], ['other', 'orphan', 'second-orphan'])
        self.assertEqual(repair.reverted_state(result, self.state, self.plan), self.state)

    def test_conflict_on_second_orphan_prevents_entire_patch(self):
        self.plan['orphans'] = ['orphan', 'second-orphan']
        self.state['thread-project-assignments']['second-orphan'] = {'projectId': 'manual'}
        with self.assertRaises(ValueError):
            repair.patched_state(self.state, self.plan)

    def test_conflicting_same_id_is_rejected(self):
        self.state['local-projects']['new'] = {'id': 'new', 'name': '人工改动'}
        with self.assertRaises(ValueError):
            repair.patched_state(self.state, self.plan)

    def test_duplicate_name_different_id_is_rejected(self):
        self.state['local-projects']['other-id'] = {'id': 'other-id', 'name': '新项目'}
        with self.assertRaises(ValueError):
            repair.patched_state(self.state, self.plan)

    def test_changed_orphan_assignment_is_not_erased(self):
        self.state['thread-project-assignments']['orphan'] = {'projectId': 'manual'}
        with self.assertRaises(ValueError):
            repair.patched_state(self.state, self.plan)

    def test_rollback_preserves_later_unrelated_changes(self):
        after = repair.patched_state(self.state, self.plan)
        after['unknown'].append(3)
        restored = repair.reverted_state(after, self.state, self.plan)
        expected = copy.deepcopy(self.state)
        expected['unknown'].append(3)
        self.assertEqual(restored, expected)

    def test_rollback_refuses_project_with_members(self):
        after = repair.patched_state(self.state, self.plan)
        after['thread-project-assignments']['moved'] = {'projectId': 'new'}
        with self.assertRaises(ValueError):
            repair.reverted_state(after, self.state, self.plan)

    def test_live_app_blocks_writes_and_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / 'state.json'
            state.write_text(json.dumps(self.state), encoding='utf-8')
            original = state.read_bytes()
            with self.assertRaises(RuntimeError):
                repair.write_patch(state, self.plan, root / 'backup', lambda: [123])
            self.assertEqual(state.read_bytes(), original)
            self.assertFalse((root / 'backup').exists())

    def test_disk_roundtrip_backup_and_revert(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / 'state.json'
            state.write_text(json.dumps(self.state), encoding='utf-8')
            original = state.read_bytes()
            backup = root / 'backup'
            result = repair.write_patch(state, self.plan, backup, lambda: [])
            self.assertEqual((backup / 'before.json').read_bytes(), original)
            self.assertEqual(json.loads(state.read_text(encoding='utf-8')), result['after'])
            again = repair.write_patch(state, self.plan, backup, lambda: [])
            self.assertEqual(again['state'], 'already_applied')
            self.assertEqual((backup / 'before.json').read_bytes(), original)

    def test_reopened_app_during_backup_blocks_replace(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / 'state.json'
            state.write_text(json.dumps(self.state), encoding='utf-8')
            original = state.read_bytes()
            calls = []
            def guard():
                calls.append(1)
                return [] if len(calls) == 1 else [123]
            with self.assertRaises(RuntimeError):
                repair.write_patch(state, self.plan, root / 'backup', guard)
            self.assertEqual(state.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
