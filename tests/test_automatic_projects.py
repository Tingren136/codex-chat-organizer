import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import automatic_projects as app
from review_workflow import digest
from offline_registration import save, load
from test_project_admin import FakeRpc


class AutomaticProjectsTests(unittest.TestCase):
    def setup_case(self, root):
        session = {'id':'demo','state':'awaiting_group_review','excluded_before':[],
            'batches':[{'result':{'items':[{'thread':'chat','state':'desktop_state_verified'}]}}]}
        approval = {'session_id':'demo','approved':True,'user_confirmation':'同意新建阅读项目并归类',
            'review_digest':digest(session),'decisions':[{'thread':'chat','destination':'new:阅读'}]}
        save(root/'session.json',session);save(root/'approval.json',approval)
        rpc=FakeRpc();rpc.close=lambda:None
        return rpc,approval

    def test_create_folder_project_then_move_and_replay_reuses(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);rpc,approval=self.setup_case(root)
            moves=[]
            def move(sid,path):
                moves.append(load(path))
                return {'state':'grouping_completed'}
            with patch.multiple(app,ROOT=root,session_path=lambda sid:root/'session.json',
                Rpc=lambda:rpc,DesktopMcp=Mock,apply_groups=move,
                call=lambda *a:{'projects':[{'projectId':'project-demo','projectKind':'local'}]}):
                self.assertEqual(app.apply('demo',root/'approval.json')['state'],'grouping_completed')
                app.apply('demo',root/'approval.json')
            self.assertEqual(rpc.creates,1)
            self.assertTrue(Path(rpc.projects[0]['roots'][0]['path']).is_dir())
            self.assertEqual(moves[0]['decisions'][0]['destination'],'project-demo')
            self.assertEqual(moves[0]['review_digest'],approval['review_digest'])

    def test_backend_missing_from_desktop_preserves_created_project(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);rpc,_=self.setup_case(root)
            move=Mock(side_effect=AssertionError('must not move yet'))
            with patch.multiple(app,ROOT=root,session_path=lambda sid:root/'session.json',
                Rpc=lambda:rpc,DesktopMcp=Mock,apply_groups=move,call=lambda *a:{'projects':[]}):
                result=app.apply('demo',root/'approval.json')
                app.apply('demo',root/'approval.json')
            self.assertEqual(rpc.creates,1)
            self.assertEqual(result['project_creation']['state'],'desktop_registration_required')
            self.assertEqual(result['project_creation']['projects']['阅读'],'project-demo')
            move.assert_not_called()

    def test_existing_same_name_reuses_original_folder(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);rpc,_=self.setup_case(root)
            rpc.projects=[{'id':'original','name':'阅读','roots':[{'path':str(root)}]}]
            with patch.multiple(app,ROOT=root,session_path=lambda sid:root/'session.json',
                Rpc=lambda:rpc,DesktopMcp=Mock,apply_groups=lambda *a:{'state':'done'},
                call=lambda *a:{'projects':[{'projectId':'original','projectKind':'local'}]}):
                app.apply('demo',root/'approval.json')
            self.assertEqual(rpc.creates,0)
            self.assertEqual(load(root/'work/automatic-projects/demo/roots.json')['阅读'],[str(root)])

    def test_unapproved_plan_cannot_create_directories_or_connect(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);_,approval=self.setup_case(root)
            approval['approved']=False;save(root/'approval.json',approval)
            rpc=Mock(side_effect=AssertionError('no connection before approval'))
            with patch.multiple(app,ROOT=root,session_path=lambda sid:root/'session.json',Rpc=rpc):
                with self.assertRaises(ValueError):app.apply('demo',root/'approval.json')
            self.assertFalse((root/'work').exists())


if __name__=='__main__':unittest.main()
