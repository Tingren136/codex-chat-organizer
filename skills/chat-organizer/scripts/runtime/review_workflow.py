"""On-demand naming batches followed by separately approved project placement."""
import argparse
import hashlib
import json
import msvcrt
from pathlib import Path
import uuid

from batch_cache import Cache, ROOT, encoded
from desktop_mcp_probe import DesktopMcp
from oneclick import DB, excluded, select, run, setup
from project_executor import call, inventory, restore_pin
from probe import Rpc
from settings import output_dir
from naming_policy import load_policy
from title_review import title_digest, title_draft, apply_titles

WORK = ROOT / 'work' / 'review-sessions'


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(encoded(value), encoding='utf-8')
    tmp.replace(path)


def session_path(sid):
    return WORK / str(uuid.UUID(sid)) / 'session.json'


def rows(session):
    return [item for child in session['batches'] for item in child.get('result', {}).get('items', [])]


def digest(session):
    return hashlib.sha256(encoded(rows(session)).encode('utf-8')).hexdigest()


def fill_candidates(cache, client, selected, count):
    if len(selected) >= count:
        return selected
    deny = sorted(i for i in excluded(cache) | set(selected) if i)
    if len(deny) > 100:
        return selected
    rpc = Rpc()
    try:
        page = rpc.call('thread/list', {'limit': count-len(selected), 'sortKey': 'updated_at',
            'useStateDbOnly': True, 'excludedThreadIds': deny,
            'sourceKinds': ['cli', 'vscode', 'appServer']})
    finally:
        rpc.close()
    candidates = [t['id'] for t in page['data']]
    native = inventory(client, candidates)
    extra = select(cache, native, candidates, count-len(selected))
    return selected + [t['id'] for t in extra]


def outputs(session, projects):
    directory = session_path(session['id']).parent
    names = {p['projectId']: p['label'] for p in projects}
    def clean(text):
        return str(text or '').replace('|', '／').replace('\n', ' ')
    lines = [f"# 聊天整理报告｜{session['id']}", '',
             f"请求 {session['requested']} 条；选中 {len(session['selected'])} 条；已输出 {len(rows(session))} 条。",
             ('标题方案待确认，尚未改名；项目归类需另行确认。' if session['state'] in ('awaiting_title_review', 'analyzing') else '标题按逐条状态展示；未确认或未完成项继续等待，项目归类按独立批准执行。'), '',
             '|序号|聊天ID|原题 → 建议题／执行后题|内容摘要|建议分组|理由|状态|',
             '|---|---|---|---|---|---|---|']
    if session.get('naming_policy'):
        lines.insert(4, '固定命名规则：' + session['naming_policy']['format'] + '；分隔符：' + session['naming_policy']['separator'] + '；最长' + str(session['naming_policy']['max_length']) + '字；规则版本' + str(session['naming_policy']['version']) + '。')
    decisions, groups = [], {}
    for n, item in enumerate(rows(session), 1):
        action = item.get('group_action') or 'review'
        pid = item.get('suggested_project')
        new_name = item.get('proposed_project_name')
        group = {'existing': names.get(pid, pid), 'new': '建议新建：' + (new_name or ''),
                 'misc': '杂聊／不强制归项目', 'keep': '保留现项目', 'review': '待判断'}.get(action)
        groups.setdefault(group or '待判断', []).append(n)
        title = item.get('after_title') or item.get('suggested_title') or item.get('before_title')
        lines.append('|'+ '|'.join(map(clean, [n, item['thread'], (item.get('before_title') or '（原无标题）')+' → '+(title or '（无标题）'),
                     item.get('summary'), group, item.get('group_reason'), item['state']]))+'|')
        # Misc is a suggestion to leave alone unless the user explicitly chooses removal.
        destination = pid if action == 'existing' else ('new:' + new_name if action == 'new' and new_name else 'keep')
        decisions.append({'thread': item['thread'], 'destination': destination,
                          'proposed_new_project': new_name if action == 'new' else None})
    lines.extend(['', '## 分组建议汇总', ''])
    for group, indices in groups.items():
        lines.append(f"- {group}：第{','.join(map(str, indices))}条。")
    lines.extend(['', '新项目现在只建议；用户确认后用apply自动准备目录、创建或复用项目并归类。后端创建与桌面登记分开核验。',
                  '现有项目归类、保留原项目或明确移为无项目，均按用户确认方案执行。'])
    text = '\n'.join(lines)
    (directory / '逐条报告.md').write_text(text, encoding='utf-8')
    reports = output_dir()
    reports.mkdir(parents=True, exist_ok=True)
    public_report = reports / ('聊天整理报告_' + session['id'] + '.md')
    public_report.write_text(text, encoding='utf-8')
    session['public_report_path'] = str(public_report)
    plan_path = directory / '分组确认草案.json'
    plan = {'session_id': session['id'], 'review_digest': digest(session),
            'approved': False, 'user_confirmation': '', 'decisions': decisions}
    # Keep any already filled user decisions; regenerate only a distinct draft.
    save(plan_path, plan)
    session['report_path'] = str(directory/'逐条报告.md')
    session['grouping_draft'] = str(plan_path)
    if session.get('naming_policy'):
        title_path = directory / '标题确认草案.json'
        save(title_path, title_draft(session, rows(session)))
        session['title_draft'] = str(title_path)


def scan(count, sid=None):
    if not 1 <= count <= 20:
        raise ValueError('Count must be 1..20')
    sid = str(uuid.UUID(sid)) if sid else str(uuid.uuid4())
    path = session_path(sid)
    if path.exists():
        session = json.loads(path.read_text(encoding='utf-8'))
        if session['requested'] != count:
            raise ValueError('Session count cannot be changed')
        if session['state'] in ('awaiting_title_review', 'title_partial', 'awaiting_group_review', 'grouping_partial', 'grouping_completed'):
            return session
        if 'naming_policy' not in session:
            raise ValueError('Legacy interrupted session: inspect cached receipts; do not rerun analysis or titles')
        if len(session['selected']) < count:
            cache = Cache(DB)
            client = DesktopMcp()
            try:
                session['selected'] = fill_candidates(cache, client, session['selected'], count)
                save(path, session)
            finally:
                cache.db.close(); client.close()
    else:
        cache = Cache(DB)
        setup(cache)
        client = DesktopMcp()
        try:
            all_threads = inventory(client)
            projects = call(client, 'list_projects', {})['projects']
            chosen = select(cache, all_threads, None, count)
            selected_ids = fill_candidates(cache, client, [t['id'] for t in chosen], count)
            session = {'id': sid, 'requested': count, 'selected': selected_ids,
                       'excluded_before': sorted(i for i in excluded(cache) if i),
                       'batches': [], 'state': 'analyzing', 'projects': projects, 'naming_policy': load_policy()}
            save(path, session)
        finally:
            cache.db.close()
            client.close()
    session.setdefault('naming_policy', load_policy())
    session['state'] = 'analyzing'
    covered = {tid for b in session['batches'] for tid in b['ids']}
    remaining = [tid for tid in session['selected'] if tid not in covered]
    chunks = [b['ids'] for b in session['batches']] + [remaining[i:i+2] for i in range(0,len(remaining),2)]
    for ids in chunks:
        child = next((b for b in session['batches'] if b['ids'] == ids), None)
        if child is None:
            child = {'ids': ids, 'run': str(uuid.uuid4())}
            session['batches'].append(child)
            save(path, session)
        if 'result' not in child:
            proposals = sorted({r['proposed_project_name'] for r in rows(session)
                                if r.get('proposed_project_name')})
            try:
                prepared = {tid: json.loads(Path(session['prepared_snapshots'][tid]).read_text(encoding='utf-8'))
                            for tid in ids if tid in session.get('prepared_snapshots', {})}
                result = run(child['run'], ids, limit=2, naming_only=True, proposed_groups=proposals,
                             prepared_snapshots=prepared, naming_policy=session['naming_policy'])
                if result.get('skipped_existing_run'):
                    result = result['stored_report']
                child['result'] = result
                if result.get('state') != 'completed':
                    raise RuntimeError('Saved batch requires review; do not resend')
            except Exception as exc:
                session.update(state='needs_review', error=str(exc))
                outputs(session, session['projects'])
                save(path, session)
                raise
            save(path, session)
    session['state'] = 'awaiting_title_review'
    outputs(session, session['projects'])
    save(path, session)
    return session


def validate_approval(session, approval):
    if session['state'] not in ('awaiting_group_review', 'grouping_partial', 'grouping_completed'):
        raise ValueError('Naming stage is not ready for project approval')
    if approval.get('approved') is not True or not str(approval.get('user_confirmation', '')).strip():
        raise ValueError('Requires explicit user approval of this concrete grouping plan')
    if approval.get('session_id') != session['id'] or approval.get('review_digest') != digest(session):
        raise ValueError('Approval refers to another or modified report')
    eligible = {r['thread'] for r in rows(session) if r.get('state') == 'desktop_state_verified'}
    selected = [d['thread'] for d in approval['decisions']]
    if len(selected) != len(set(selected)) or not set(selected) <= eligible:
        raise ValueError('Decisions include duplicate/foreign/unverified chats')
    if set(selected) & set(session['excluded_before']):
        raise ValueError('Pre-existing protected chat must never be regrouped')


def apply_groups(sid, approval_path):
    path = session_path(sid)
    session = json.loads(path.read_text(encoding='utf-8'))
    approval = json.loads(Path(approval_path).read_text(encoding='utf-8-sig'))
    validate_approval(session, approval)
    client = DesktopMcp(allow_project_move=True, allow_pin_restore=True)
    cache = Cache(DB)
    cache.db.execute('''CREATE TABLE IF NOT EXISTS review_group_actions(
        session TEXT,thread TEXT,state TEXT,receipt TEXT,PRIMARY KEY(session,thread))''')
    try:
        catalog = call(client, 'list_projects', {})['projects']
        projects = {p['projectId']: p for p in catalog if p.get('projectKind') == 'local'}
        by_id = {r['thread']: r for r in rows(session)}
        initial = inventory(client, list(by_id))
        receipts = []
        for decision in approval['decisions']:
            tid, target = decision['thread'], decision['destination']
            old = cache.db.execute('SELECT state,receipt FROM review_group_actions WHERE session=? AND thread=?', (sid, tid)).fetchone()
            if old:
                receipts.append({'thread': tid, 'state': old['state'], 'skipped_already_attempted': True})
                continue
            if target == 'keep':
                receipts.append({'thread': tid, 'state': 'kept'})
                continue
            if target is not None and target not in projects:
                receipts.append({'thread': tid, 'state': 'needs_existing_project_id',
                                 'reason': 'New project must first exist in official desktop catalog'})
                continue
            before = inventory(client, [tid]).get(tid)
            named = by_id[tid]
            if not before or before.get('status') == 'active' or before['title'] != named['after_title'] or before.get('projectId') != named['after_project']:
                receipts.append({'thread': tid, 'state': 'external_change'})
                continue
            if named.get('after_updated_at') is not None and before.get('updatedAt') != named['after_updated_at']:
                receipts.append({'thread': tid, 'state': 'content_changed_after_report'})
                continue
            if target == before.get('projectId'):
                receipts.append({'thread': tid, 'state': 'already_at_target'})
                continue
            with cache.db:
                cache.db.execute("INSERT INTO review_group_actions VALUES(?,?,'attempted',NULL)", (sid, tid))
            receipt = {'thread': tid, 'target': target, 'before': before}
            try:
                raw = client.request('tools/call', {'name': 'move_thread_to_project',
                      'arguments': {'threadId': tid, 'projectId': target, 'hostId': 'local', 'source': 'codex'}})
                receipt['tool_response'] = raw
                after = inventory(client, [tid]).get(tid)
                if after and after.get('projectId') == target:
                    receipt['pin_restore'] = restore_pin(cache.db, client, tid, before, initial)
                    after = inventory(client, [tid]).get(tid)
                verified = bool(after and after.get('projectId') == target and all(after.get(k) == before.get(k) for k in ('title','cwd','pinnedIndex')))
                receipt.update(after=after, state='verified' if verified else 'needs_review')
            except Exception as exc:
                receipt.update(state='needs_review', error=str(exc))
            with cache.db:
                cache.db.execute('UPDATE review_group_actions SET state=?,receipt=? WHERE session=? AND thread=?',
                                 (receipt['state'], encoded(receipt), sid, tid))
            receipts.append(receipt)
            if receipt['state'] == 'needs_review':
                break
        session['group_receipts'] = receipts
        final = inventory(client, list(initial))
        approved_ids = {d['thread'] for d in approval['decisions']}
        protected_checks = {tid: bool(tid in final and all(before.get(k) == final[tid].get(k)
                           for k in ('title','projectId','cwd','pinnedIndex')))
                           for tid, before in initial.items() if tid not in approved_ids}
        session['group_protected_unchanged'] = protected_checks
        session['state'] = 'grouping_completed' if len(approval['decisions']) == len(rows(session)) and len(receipts) == len(approval['decisions']) and all(
            r['state'] in ('verified','kept','already_at_target') for r in receipts) else 'grouping_partial'
        if not all(protected_checks.values()):
            session['state'] = 'grouping_partial'
            session['error'] = 'Protected item changed externally; review only'
        save(path, session)
        return session
    finally:
        cache.db.close()
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('scan')
    p.add_argument('--count', type=int, default=20)
    p.add_argument('--session-id')
    p = sub.add_parser('report')
    p.add_argument('--session-id', required=True)
    p = sub.add_parser('apply-titles')
    p.add_argument('--session-id', required=True)
    p.add_argument('--approval', type=Path, required=True)
    p = sub.add_parser('apply')
    p.add_argument('--session-id', required=True)
    p.add_argument('--approval', type=Path, required=True)
    args = parser.parse_args()
    lock = (ROOT/'work'/'oneclick.lock').open('a+b')
    try:
        lock.seek(0)
        if not lock.read(1):
            lock.write(b'0'); lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        if args.command == 'scan':
            result = scan(args.count, args.session_id)
        elif args.command == 'apply-titles':
            result = apply_titles(args.session_id, args.approval, session_path, outputs, save, DB)
        elif args.command == 'apply':
            from automatic_projects import apply
            result = apply(args.session_id, args.approval)
        else:
            result = json.loads(session_path(args.session_id).read_text(encoding='utf-8'))
        print(encoded({'session': result['id'], 'state': result['state'],
                       'requested': result['requested'], 'selected': len(result['selected']),
                       'reported': len(rows(result)), 'report': result.get('report_path'),
                       'title_draft': result.get('title_draft'),
                       'grouping_draft': result.get('grouping_draft'),
                       'project_creation': result.get('project_creation'),
                       'group_receipts': result.get('group_receipts')}))
    finally:
        lock.close()


if __name__ == '__main__':
    main()
