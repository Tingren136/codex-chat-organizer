import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from batch_cache import Cache, encoded, fingerprint
from naming_policy import DEFAULT, load_policy, validate_title
import title_review as app
from test_review_workflow import NoScreenClient


class TitleReviewTests(unittest.TestCase):
    def session(self):
        before = {'id':'new', 'title':'old title', 'projectId':None,'cwd':'same',
                  'status':'idle','kind':'codex','hostId':'local','updatedAt':1}
        return {'id':'session','requested':1,'selected':['new'],'excluded_before':['old'],
                'projects':[], 'state':'awaiting_title_review', 'naming_policy':dict(DEFAULT),
                'batches':[{'result':{'items':[{'thread':'new','before':before,
                  'before_title':'old title','suggested_title':'阅读｜笔记整理',
                  'content_hash':fingerprint([]),'title_action':'rename','state':'awaiting_title_review'}]}}]}

    def test_approval_rejects_stale_policy_foreign_duplicates_and_invalid_title(self):
        session=self.session()
        plan=app.title_draft(session,app.items(session))
        plan.update(approved=True,user_confirmation='approved')
        app.validate(session,copy.deepcopy(plan))
        for mutate in (
            lambda p:p.update(title_digest='stale'),
            lambda p:p['decisions'].append(dict(p['decisions'][0])),
            lambda p:p['decisions'][0].update(thread='old'),
            lambda p:p['decisions'][0].update(title='no separator'),
            lambda p:p.update(user_confirmation=''),
        ):
            changed=copy.deepcopy(plan);mutate(changed)
            with self.assertRaises(ValueError):app.validate(session,changed)
        session['naming_policy']['version']=2
        with self.assertRaises(ValueError):app.validate(session,plan)

    def test_policy_persists_and_validates_without_model(self):
        with tempfile.TemporaryDirectory() as d, patch('naming_policy.data_root',lambda:Path(d)):
            self.assertEqual(load_policy(),DEFAULT)
            path=Path(d)/'naming-policy.json'
            custom=dict(DEFAULT,version=2,separator=' · ',max_length=24)
            path.write_text(encoded(custom),encoding='utf-8')
            self.assertEqual(load_policy(),custom)
            validate_title('阅读 · 笔记整理',custom)
            with self.assertRaises(ValueError):validate_title('阅读｜笔记整理',custom)

    def execute(self, mutate=None, client=NoScreenClient):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path=root/'session.json';db=root/'db.sqlite'
            session=self.session()
            plan=app.title_draft(session,app.items(session));plan.update(approved=True,user_confirmation='approved')
            approval=root/'approval.json';approval.write_text(encoded(plan),encoding='utf-8')
            path.write_text(encoded(session),encoding='utf-8')
            NoScreenClient.writes=0
            NoScreenClient.data={'new':dict(app.items(session)[0]['before'])}
            if mutate:mutate(NoScreenClient.data['new'])
            def save(p,v):p.write_text(encoded(v),encoding='utf-8')
            with patch.multiple(app, DesktopMcp=client, Rpc=lambda:NoScreenClient(),
                                read_snapshot=lambda rpc,tid:{'messages':[]}):
                for _ in range(2):
                    result=app.apply_titles('session',approval,lambda sid:path,lambda s,p:None,save,db)
                return result,NoScreenClient.writes

    def test_external_change_prevents_write(self):
        result,writes=self.execute(lambda row:row.update(updatedAt=2))
        self.assertEqual(writes,0)
        self.assertEqual(result['state'],'title_partial')

    def test_lost_reply_is_not_replayed(self):
        class LostReply(NoScreenClient):
            def request(self,method,params):
                result=super().request(method,params)
                if params['name']=='set_thread_title':raise RuntimeError('response lost')
                return result
        result,writes=self.execute(client=LostReply)
        self.assertEqual(writes,1)
        self.assertEqual(result['state'],'title_partial')
        self.assertTrue(result['title_receipts'][0]['skipped_already_attempted'])

    def test_success_replay_no_duplicate_write(self):
        result,writes=self.execute()
        self.assertEqual(writes,1)
        self.assertEqual(result['state'],'awaiting_group_review')


if __name__=='__main__':unittest.main()
