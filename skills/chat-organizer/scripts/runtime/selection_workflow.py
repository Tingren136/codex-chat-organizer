"""Inventory first; count actual rename proposals, not inspected chats."""
import copy
import json
import os
from pathlib import Path
import uuid
from collections import Counter
from batch_cache import Cache, encoded
from oneclick import DB, ROOT, setup, run
from naming_policy import load_policy, validate_title
from probe import Rpc
from desktop_mcp_probe import DesktopMcp
from project_executor import call
import review_workflow as workflow


def rename_needed(item):
    return item.get('title_action') == 'rename' and bool(item.get('suggested_title')) and item['suggested_title'] != item.get('before_title')


def saved_proposals():
    found = {}
    for path in sorted(workflow.WORK.glob('*/session.json')):
        session = json.loads(path.read_text(encoding='utf-8'))
        for batch in session.get('batches', []):
            for item in batch.get('result', {}).get('items', []):
                if item.get('state') == 'awaiting_title_review' and item.get('before') and item.get('content_hash'):
                    found.setdefault(item['thread'], {'item':item, 'source_session':session['id'],
                                                     'policy':session.get('naming_policy')})
    return found


def catalog(rpc, recent=None):
    found, cursors, cursor = {}, set(), None
    # Exhaust pages; never mistake the native sidebar's 50-row window for all chats.
    for _ in range(1000):
        params = {'limit':100, 'sortKey':'updated_at', 'sortDirection':'desc',
                  'useStateDbOnly':True, 'sourceKinds':['cli','vscode','appServer']}
        if cursor:params['cursor']=cursor
        page=rpc.call('thread/list', params)
        for t in page['data']:
            found.setdefault(t['id'], t)
            if recent and len(found) >= recent:
                return list(found.values()), 'recent_limit'
        cursor=page.get('nextCursor')
        if not cursor:return list(found.values()), 'exhausted'
        if cursor in cursors:raise RuntimeError('Repeated catalog cursor; inventory is incomplete')
        cursors.add(cursor)
    raise RuntimeError('Catalog page safety limit reached; inventory is incomplete')


def discover(recent=None):
    if recent is not None and recent < 1:raise ValueError('recent must be positive')
    cache=Cache(DB);setup(cache)
    rpc=Rpc()
    try:
        metadata, boundary=catalog(rpc,recent)
        locked={r[0] for r in cache.db.execute('SELECT thread FROM locks')}
        locked |= {r[0] for r in cache.db.execute('SELECT thread FROM unified_actions')}
        snapshots={r[0] for r in cache.db.execute('SELECT DISTINCT thread FROM snapshots')}
        deferred={r['thread']:dict(r) for r in cache.db.execute('SELECT * FROM deferred_reads')}
        saved=saved_proposals()
        entries=[]
        for t in metadata:
            tid=t['id']; status=t.get('status')
            if isinstance(status,dict):status=status.get('type')
            row={'thread':tid,'title':t.get('name') or t.get('title') or '',
                 'updated_at':t.get('updatedAt'),'metadata':t}
            if tid == os.environ.get('CODEX_THREAD_ID') or status == 'active':reason='active'
            elif t.get('archived'):reason='archived'
            elif tid in locked:reason='protected'
            elif tid in saved:
                reason='cached';row['cached']=saved[tid]
            elif tid in deferred:
                reason='deferred';row['deferred']=deferred[tid]
            elif tid in snapshots:reason='previous_analysis_needs_review'
            else:reason='candidate'
            row['status']=reason;entries.append(row)
        return {'scope':'recent_'+str(recent) if recent else 'all_available_local_nonarchived',
                'catalog_boundary':boundary,'listed':len(entries),'counts':dict(Counter(x['status'] for x in entries)),
                'entries':entries,'model_calls':0,'chat_mutations':0}
    finally:
        rpc.close();cache.db.close()


def write_inventory(value):
    directory=ROOT/'work'/'inventories';directory.mkdir(parents=True,exist_ok=True)
    path=directory/(str(uuid.uuid4())+'.json');workflow.save(path,value)
    from settings import output_dir
    reports=output_dir();reports.mkdir(parents=True,exist_ok=True)
    report=reports/(path.stem+'_聊天盘点.md')
    clean=lambda x:str(x or '').replace('|','／').replace('\n',' ')
    lines=['# 聊天范围盘点','',f"列出 {value['listed']} 条；仅读标题元数据，未调用模型、未改名。",
           'candidate表示尚未分析，不代表已确认标题混乱；cached表示可复用旧分析。',
           '|序号|聊天ID|原标题|状态|','|---|---|---|---|']
    lines += ['|'+'|'.join(clean(x) for x in (i,e['thread'],e['title'],e['status']))+'|'
              for i,e in enumerate(value['entries'],1)]
    report.write_text('\n'.join(lines),encoding='utf-8')
    return {k:v for k,v in value.items() if k!='entries'} | {'inventory':str(path),'report':str(report)}


def finish(session, path, reason):
    session['selection_stop']=reason
    session['state']='awaiting_title_review' if workflow.rows(session) else 'no_changes'
    counts=Counter(x['outcome'] for x in session['examined'])
    session['selection_counts']=dict(counts, rename_proposals=len(workflow.rows(session)),
        examined=len(session['examined']), remaining=max(0,len(session['queue'])-session['position']))
    workflow.outputs(session,session['projects']);workflow.save(path,session)
    return session


def scan(target=None, sid=None, all_mode=False, recent=None, max_analysis=None):
    if (target is None) == (not all_mode):
        raise ValueError('Choose either an actual rename count or --all')
    if target is not None and (type(target) is not int or target<1):raise ValueError('Count must be positive')
    if max_analysis is not None and max_analysis<1:raise ValueError('max-analysis must be positive')
    sid=str(uuid.UUID(sid)) if sid else str(uuid.uuid4());path=workflow.session_path(sid)
    if path.exists():
        session=json.loads(path.read_text(encoding='utf-8'))
        if session.get('selection_mode')!='rename_count':
            raise ValueError('Legacy session: read cached report; start scoped scan to reuse pending proposals')
        if any(session.get(k)!=v for k,v in [('target',target),('all_mode',all_mode),('recent',recent),('max_analysis',max_analysis)]):
            raise ValueError('Resume must preserve original scope and budget')
        if session['state']!='selecting':return session
    else:
        listing=discover(recent)
        client=DesktopMcp()
        try:projects=call(client,'list_projects',{})['projects']
        finally:client.close()
        queue=[e for e in listing['entries'] if e['status'] in ('candidate','cached')]
        session={'id':sid,'requested':target if target else len(queue),'selected':[],
            'excluded_before':[e['thread'] for e in listing['entries'] if e['status'] in ('protected','active','archived')],
            'batches':[],'projects':projects,'state':'selecting','selection_mode':'rename_count',
            'target':target,'all_mode':all_mode,'recent':recent,'max_analysis':max_analysis,
            'naming_policy':load_policy(),'queue':queue,'position':0,'examined':[],
            'catalog_counts':listing['counts'],'catalog_listed':listing['listed'],
            'catalog_boundary':listing['catalog_boundary'], 'new_model_calls':0,
            'prior_deferred':[e for e in listing['entries'] if e['status'] in ('deferred','previous_analysis_needs_review')]}
        workflow.save(path,session)
    while session['position']<len(session['queue']):
        if target and len(workflow.rows(session))>=target:return finish(session,path,'target_reached')
        entry=session['queue'][session['position']];tid=entry['thread']
        child=next((b for b in session['batches'] if b['ids']==[tid]),None)
        if child is None:
            child={'ids':[tid],'run':str(uuid.uuid4())};session['batches'].append(child)
            session['selected'].append(tid);workflow.save(path,session)
        if 'result' not in child:
            if entry['status']=='cached':
                cached=entry['cached'];item=copy.deepcopy(cached['item'])
                before=item['before'];meta=entry['metadata']
                changed=(entry['title']!=before.get('title') or
                         (before.get('updatedAt') is not None and meta.get('updatedAt')!=before.get('updatedAt')))
                if changed or cached['policy']!=session['naming_policy']:
                    child['result']={'state':'completed','items':[],'deferred':[{'reason':'cached_changed_or_policy_differs'}]}
                else:
                    child['result']={'state':'completed','items':[item],'model_calls':0}
                    child['reused_from_session']=cached['source_session']
            else:
                if max_analysis and session['new_model_calls']>=max_analysis:
                    return finish(session,path,'analysis_budget_reached')
                try:
                    result=run(child['run'],[tid],limit=1,naming_only=True,
                        naming_policy=session['naming_policy'], proposed_groups=sorted({r['proposed_project_name']
                        for b in session['batches'] for r in b.get('result',{}).get('items',[]) if r.get('proposed_project_name')}))
                    if result.get('skipped_existing_run'):result=result['stored_report']
                    child['result']=result
                    session['new_model_calls']+=result.get('model_calls',0)
                except Exception as exc:
                    # No ambiguous model request is resent. Preserve this run id.
                    session['error']=str(exc)
                    return finish(session,path,'analysis_error_no_retry')
            workflow.save(path,session)
        result=child['result']
        if result.get('state') not in ('completed','no_new_work'):
            return finish(session,path,'analysis_error_no_retry')
        items=result.get('items',[])
        outcome='deferred' if result.get('deferred') else 'skipped'
        if items:
            item=items[0]
            if rename_needed(item):
                try:validate_title(item['suggested_title'],session['naming_policy'])
                except ValueError:item['title_action']='review'
            outcome='rename' if rename_needed(item) else ('keep' if item.get('title_action')=='keep' else 'review')
        session['examined'].append({'thread':tid,'title':entry['title'],'outcome':outcome,
            'reused':bool(child.get('reused_from_session')),'details':result.get('deferred') or result.get('skipped',[])})
        session['position']+=1;workflow.save(path,session)
    return finish(session,path,'target_reached' if target and len(workflow.rows(session))>=target else 'scope_exhausted')
