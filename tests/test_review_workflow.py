import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import review_workflow as workflow
import title_review
from naming_policy import DEFAULT
from batch_cache import Cache, encoded, fingerprint


class NoScreenClient:
    writes = 0
    data = {}
    def __init__(self, **kwargs):
        pass
    def request(self, method, params):
        tool, args = params['name'], params['arguments']
        if tool == 'list_projects':
            data = {'projects': [{'projectId': 'dest', 'label': 'Existing', 'projectKind': 'local'}]}
        elif tool == 'list_threads':
            data = {'threads': list(self.data.values()), 'pinnedThreads': []}
        elif tool == 'move_thread_to_project':
            NoScreenClient.writes += 1
            self.data[args['threadId']]['projectId'] = args['projectId']
            data = {'ok': True}
        elif tool == 'set_thread_title':
            NoScreenClient.writes += 1
            self.data[args['threadId']]['title'] = args['title']
            data = {'ok': True}
        else:
            raise AssertionError('Unexpected call: '+tool)
        return {'content': [{'type': 'text', 'text': encoded(data)}]}
    def close(self):
        pass


class ReviewTests(unittest.TestCase):
    def test_twenty_names_report_before_approved_groups(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            db = root/'cache.db'
            c = Cache(db)
            with c.db:
                c.db.execute("INSERT INTO locks VALUES('old','protected')")
            c.db.close()
            NoScreenClient.writes = 0
            NoScreenClient.data = {str(i): {'id': str(i), 'title': 'Old '+str(i),
                'kind': 'codex', 'hostId': 'local', 'status': 'idle', 'projectId': None, 'cwd': 'same'} for i in range(20)}
            NoScreenClient.data['old'] = {'id': 'old', 'title': 'protected', 'kind': 'codex',
                'hostId': 'local', 'status': 'idle', 'projectId': None}
            calls = []
            def name_batch(run_id, ids, **kwargs):
                self.assertTrue(kwargs['naming_only'])
                self.assertNotIn('old', ids)
                calls.append(ids)
                items = []
                for tid in ids:
                    items.append({'thread': tid, 'state': 'awaiting_title_review',
                      'before': dict(NoScreenClient.data[tid]), 'content_hash': fingerprint([]),
                      'title_action': 'rename', 'suggested_title': '主题｜任务'+tid,
                      'before_title': 'Old '+tid,
                      'group_action': 'existing', 'suggested_project': 'dest',
                      'summary': 'summary', 'group_reason': 'reason'})
                return {'state': 'completed', 'items': items}
            sid = 'f28f906e-783b-4c03-bcc2-9f4a138c2b79'
            with patch.multiple(workflow, ROOT=root, WORK=root/'sessions', DB=db,
                                output_dir=lambda: root/'reports',
                                load_policy=lambda: dict(DEFAULT),
                                DesktopMcp=NoScreenClient, run=name_batch), patch.multiple(
                                title_review, DesktopMcp=NoScreenClient,
                                Rpc=lambda: NoScreenClient(), read_snapshot=lambda rpc, tid: {'messages': []}):
                session = workflow.scan(20, sid)
                self.assertEqual(len(workflow.rows(session)), 20)
                self.assertEqual(len(calls), 10)
                self.assertEqual(NoScreenClient.writes, 0)
                self.assertTrue(Path(session['public_report_path']).is_file())
                workflow.scan(20, sid)
                self.assertEqual(len(calls), 10)
                approval = json.loads(Path(session['grouping_draft']).read_text(encoding='utf-8'))
                approval.update(approved=True, user_confirmation='group approval too early')
                with self.assertRaises(ValueError):
                    workflow.validate_approval(session, approval)
                title_plan = json.loads(Path(session['title_draft']).read_text(encoding='utf-8'))
                title_file = root/'titles.json'
                title_file.write_text(encoded(title_plan), encoding='utf-8')
                with self.assertRaises(ValueError):
                    title_review.apply_titles(sid, title_file, workflow.session_path, workflow.outputs, workflow.save, db)
                self.assertEqual(NoScreenClient.writes, 0)
                title_plan.update(approved=True, user_confirmation='approve displayed titles')
                partial = dict(title_plan, decisions=title_plan['decisions'][:1])
                title_file.write_text(encoded(partial), encoding='utf-8')
                session = title_review.apply_titles(sid, title_file, workflow.session_path, workflow.outputs, workflow.save, db)
                self.assertEqual(session['state'], 'title_partial')
                self.assertEqual(NoScreenClient.writes, 1)
                with self.assertRaises(ValueError):
                    workflow.validate_approval(session, approval)
                title_file.write_text(encoded(title_plan), encoding='utf-8')
                session = title_review.apply_titles(sid, title_file, workflow.session_path, workflow.outputs, workflow.save, db)
                self.assertEqual(session['state'], 'awaiting_group_review')
                self.assertEqual(NoScreenClient.writes, 20)
                title_review.apply_titles(sid, title_file, workflow.session_path, workflow.outputs, workflow.save, db)
                self.assertEqual(NoScreenClient.writes, 20)
                self.assertEqual(len(calls), 10)
                approval = json.loads(Path(session['grouping_draft']).read_text(encoding='utf-8'))
                approval.update(approved=True, user_confirmation='User approved these destinations')
                approval['decisions'][0]['thread'] = 'old'
                with self.assertRaises(ValueError):
                    workflow.validate_approval(session, approval)
                approval['decisions'][0]['thread'] = '0'
                approval_path = root/'approved.json'
                approval_path.write_text(encoded(approval), encoding='utf-8')
                self.assertEqual(workflow.apply_groups(sid, approval_path)['state'], 'grouping_completed')
                self.assertEqual(NoScreenClient.writes, 40)
                workflow.apply_groups(sid, approval_path)
                self.assertEqual(NoScreenClient.writes, 40)
                self.assertIsNone(NoScreenClient.data['old']['projectId'])

    def test_changed_report_invalidates_confirmation(self):
        session = {'id': 'test', 'state': 'awaiting_group_review', 'excluded_before': [],
                   'batches': [{'result': {'items': []}}]}
        approval = {'session_id': 'test', 'approved': True, 'user_confirmation': 'yes',
                    'review_digest': 'wrong', 'decisions': []}
        with self.assertRaises(ValueError):
            workflow.validate_approval(session, approval)


if __name__ == '__main__':
    unittest.main()
