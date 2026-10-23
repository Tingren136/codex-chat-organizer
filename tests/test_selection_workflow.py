import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import selection_workflow as app
import review_workflow as workflow
import title_review
from naming_policy import DEFAULT
from batch_cache import Cache, encoded
import oneclick
from test_oneclick import FakeClient, FakeRpc

SID='00000000-0000-4000-8000-000000000080'


def item(tid,action='rename'):
    return {'thread':tid,'before_title':tid,'suggested_title':'主题｜整理'+tid if action=='rename' else tid,
            'title_action':action,'state':'awaiting_title_review','before':{'title':tid},
            'content_hash':'test-hash','summary':'summary','group_action':'keep','group_reason':'reason'}


def listing(ids):
    return {'entries':[{'thread':tid,'title':tid,'status':'candidate','metadata':{'name':tid}} for tid in ids],
            'listed':len(ids),'counts':{'candidate':len(ids)},'catalog_boundary':'exhausted'}


class SelectionTests(unittest.TestCase):
    def execute(self,outcomes,target=None,all_mode=False,limit=None,cached=False):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); data=listing(list(outcomes)); calls=[]
            if cached:
                data['entries'][0].update(status='cached',cached={'item':item('a'),
                    'source_session':'old-session','policy':dict(DEFAULT)})
            def fake_run(run_id,ids,**kwargs):
                tid=ids[0];calls.append(tid)
                if outcomes[tid]=='long':return {'state':'completed','items':[], 'deferred':[{'reason':'long_history'}]}
                return {'state':'completed','model_calls':1,'items':[item(tid,outcomes[tid])]}
            with patch.multiple(workflow,WORK=root/'sessions',output_dir=lambda:root/'reports'), patch.multiple(
                    app,discover=lambda recent:data,DesktopMcp=Mock,call=lambda *a:{'projects':[]},
                    load_policy=lambda:dict(DEFAULT),run=fake_run):
                result=app.scan(target,SID,all_mode,max_analysis=limit)
                repeat=app.scan(target,SID,all_mode,max_analysis=limit)
                self.assertEqual(result,repeat)
                draft=__import__('json').loads(Path(result['title_draft']).read_text(encoding='utf-8'))
                return copy.deepcopy(result),calls,draft

    def test_count_is_renames_kept_and_long_do_not_consume_slots(self):
        result,calls,draft=self.execute({'a':'keep','b':'long','c':'rename','d':'keep','e':'rename','f':'rename'},2)
        self.assertEqual(calls,['a','b','c','d','e'])
        self.assertEqual([r['thread'] for r in workflow.rows(result)],['c','e'])
        self.assertEqual([r['thread'] for r in title_review.items(result)],['c','e'])
        self.assertEqual([r['thread'] for r in draft['decisions']],['c','e'])
        self.assertEqual(result['selection_counts']['keep'],2)
        self.assertEqual(result['selection_counts']['deferred'],1)
        self.assertEqual(result['selection_counts']['remaining'],1)
        self.assertEqual(result['selection_stop'],'target_reached')

    def test_all_does_not_stop_at_twenty(self):
        result,calls,_=self.execute({str(i):'rename' for i in range(25)},all_mode=True)
        self.assertEqual(len(calls),25)
        self.assertEqual(len(workflow.rows(result)),25)
        self.assertEqual(result['selection_stop'],'scope_exhausted')

    def test_not_enough_reports_shortfall_instead_of_filling_with_keeps(self):
        result,calls,_=self.execute({'a':'keep','b':'rename'},10)
        self.assertEqual(len(workflow.rows(result)),1)
        self.assertEqual(result['selection_stop'],'scope_exhausted')

    def test_cached_proposal_counts_without_reanalysis(self):
        result,calls,_=self.execute({'a':'rename','b':'rename'},2,cached=True)
        self.assertEqual(calls,['b'])
        self.assertEqual(result['new_model_calls'],1)
        self.assertTrue(result['examined'][0]['reused'])

    def test_optional_spend_cap_is_explicit(self):
        result,calls,_=self.execute({'a':'keep','b':'rename'},2,limit=1)
        self.assertEqual(calls,['a'])
        self.assertEqual(result['selection_stop'],'analysis_budget_reached')

    def test_no_silent_default_scope(self):
        with self.assertRaises(ValueError):app.scan()

    def test_pagination_continues_beyond_first_page(self):
        class Rpc:
            def __init__(self):self.cursors=[]
            def call(self,method,args):
                self.cursors.append(args.get('cursor'))
                return {'data':[{'id':str(i)} for i in range(100)],'nextCursor':'page2'} if 'cursor' not in args else {'data':[{'id':'last'}],'nextCursor':None}
        rpc=Rpc();rows,stop=app.catalog(rpc)
        self.assertEqual(len(rows),101);self.assertEqual(rpc.cursors,[None,'page2'])
        self.assertEqual(stop,'exhausted')

    def test_repeated_cursor_is_not_called_complete(self):
        rpc=Mock();rpc.call.return_value={'data':[],'nextCursor':'again'}
        with self.assertRaises(RuntimeError):app.catalog(rpc)

    def test_long_history_is_deferred_without_model(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'work').mkdir();db=root/'cache.db'
            def snap(rpc,tid):return {'meta':{'id':tid,'name':tid,'cwd':'same'},'messages':[{'text':'x'*100001}],'complete':True}
            model=Mock(side_effect=AssertionError('long chat must not call model'))
            with patch.multiple(oneclick,ROOT=root,DB=db,DesktopMcp=FakeClient,Rpc=FakeRpc,
                                read_snapshot=snap,analyze=model,load_policy=lambda:dict(DEFAULT)):
                result=oneclick.run('long-run',['new1'],limit=1)
                self.assertEqual(result['state'],'completed')
                self.assertEqual(result['deferred'][0]['reason'],'long_history')
                self.assertEqual(result['items'],[])
                cache=Cache(db)
                self.assertEqual(cache.db.execute('select count(*) from deferred_reads').fetchone()[0],1)
                self.assertEqual(cache.db.execute('select count(*) from snapshots').fetchone()[0],0)
                cache.db.close()
            model.assert_not_called()


if __name__=='__main__':unittest.main()
