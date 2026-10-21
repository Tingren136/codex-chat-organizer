import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from batch_cache import Cache
from review_workflow import digest
import project_admin as admin
from test_project_admin import FakeRpc


class PreparationTests(unittest.TestCase):
    def test_approved_project_prepare_and_source_repair_use_no_model_or_chat_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sid = '00000000-0000-4000-8000-000000000001'
            session = {'id': sid, 'state': 'awaiting_group_review', 'selected': ['demo'],
                       'excluded_before': ['protected'], 'batches': [{'result': {'items': [{
                         'thread': 'demo', 'state': 'desktop_state_verified', 'after_title': '已命名',
                         'after_project': None, 'after_updated_at': 1}]}}]}
            approval = {'session_id': sid, 'review_digest': digest(session), 'approved': True,
                        'user_confirmation': '同意新建示例项目并归入该条',
                        'decisions': [{'thread': 'demo', 'destination': 'new:示例项目'}]}
            for name, value in [('session.json', session), ('approval.json', approval),
                                 ('roots.json', {'示例项目': [folder]}),
                                 ('.codex-global-state.json', {'local-projects': {},
                                  'project-order': [], 'thread-project-assignments': {},
                                  'projectless-thread-ids': []})]:
                (root / name).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
            cache = Cache(root / 'work/organizer-cache.sqlite')
            cache.db.execute("INSERT INTO locks VALUES('protected','keep')")
            cache.db.commit(); cache.db.close()
            before = {'demo': {'title': '已命名', 'projectId': None, 'cwd': folder,
                               'pinnedIndex': None, 'updatedAt': 1, 'status': 'idle'},
                      'protected': {'title': '不动', 'projectId': 'old', 'cwd': folder}}
            rpc = FakeRpc(); rpc.close = lambda: None
            class Client:
                def close(self):
                    pass
            server = root / ('OpenAI.Codex_' + admin.VALIDATED_OFFLINE_BUILD) / 'app/resources/plugins/openai-bundled/plugins/codex-app-tools/server.mjs'
            core = {tid: {'id': tid, 'project_id': None, 'source': 'vscode', 'archived': 0}
                    for tid in before}
            with patch.multiple(admin, ROOT=root, session_path=lambda _: root/'session.json',
                 codex_home=lambda: root, Rpc=lambda: rpc, DesktopMcp=Client,
                 inventory=lambda *args: before, core_rows=lambda *args: core,
                 desktop_server=lambda: server, call=lambda *args: {'projects': []}):
                # Creating a backend project does not implicitly authorize offline source repair.
                with self.assertRaises(ValueError):
                    admin.prepare(sid, root/'approval.json', root/'roots.json')
                self.assertEqual(rpc.creates, 1)
                result = admin.prepare(sid, root/'approval.json', root/'roots.json', '同意准备退出后的定点修复')
                self.assertEqual(result['state'], 'waiting_for_offline_repair')
                plan = json.loads(Path(result['plan']).read_text(encoding='utf-8'))
                self.assertEqual(plan['orphans'], ['demo'])
                self.assertEqual(len(plan['pending']), 1)
                self.assertEqual(plan['pending'][0]['target'], 'project-demo')
                self.assertEqual(plan['app_exe'], str(server.parents[5] / 'ChatGPT.exe'))
                original = json.loads((root/'.codex-global-state.json').read_text(encoding='utf-8'))
                self.assertEqual(original['local-projects'], {})
                self.assertEqual(rpc.creates, 1)
                again = admin.prepare(sid, root/'approval.json', root/'roots.json', '再次查看')
                self.assertEqual(again['state'], 'existing_plan_no_replay')
                self.assertEqual(rpc.creates, 1)


if __name__ == '__main__':
    unittest.main()
