"""Explicit title approval; no model calls, project writes or automatic replay."""
import hashlib
import json
from pathlib import Path
from batch_cache import Cache, encoded, fingerprint
from desktop_mcp_probe import DesktopMcp
from oneclick import setup, read_snapshot
from probe import Rpc
from project_executor import inventory
from naming_policy import validate_title


def items(session):
    result = [r for b in session['batches'] for r in b.get('result', {}).get('items', [])]
    if session.get('selection_mode') == 'rename_count':
        result = [r for r in result if r.get('title_action') == 'rename' and r.get('suggested_title')
                  and r['suggested_title'] != r.get('before_title')]
    return result


def title_digest(session, rows):
    stable = [{k: r.get(k) for k in ('thread', 'before', 'before_title', 'suggested_title',
              'title_action', 'content_hash')} for r in rows]
    return hashlib.sha256(encoded({'policy': session.get('naming_policy'), 'items': stable}).encode()).hexdigest()


def title_draft(session, rows):
    return {'session_id': session['id'], 'title_digest': title_digest(session, rows),
            'approved': False, 'user_confirmation': '', 'decisions': [
                {'thread': r['thread'], 'action': r.get('title_action', 'review'),
                 'title': r.get('suggested_title')} for r in rows]}


def validate(session, approval):
    if session['state'] not in ('awaiting_title_review', 'title_partial', 'awaiting_group_review',
                                'grouping_partial', 'grouping_completed'):
        raise ValueError('Title proposals are not ready')
    if approval.get('approved') is not True or not str(approval.get('user_confirmation', '')).strip():
        raise ValueError('Requires explicit user confirmation of titles')
    if approval.get('session_id') != session['id'] or approval.get('title_digest') != title_digest(session, items(session)):
        raise ValueError('Title approval does not match this report/policy')
    choices = approval.get('decisions', [])
    ids = [d['thread'] for d in choices]
    known = {r['thread']: r for r in items(session)}
    if not ids or len(ids) != len(set(ids)) or not set(ids) <= set(known) or set(ids) & set(session['excluded_before']):
        raise ValueError('Empty/duplicate/foreign/protected title selection')
    for d in choices:
        if d['action'] not in ('rename', 'keep'):
            raise ValueError('Resolve every approved title as rename or keep')
        if d['action'] == 'rename':
            validate_title(d['title'], session['naming_policy'])
        else:
            d['title'] = known[d['thread']]['before_title']
    return known


def apply_titles(sid, approval_path, session_path, outputs, save, db_path):
    path = session_path(sid)
    session = json.loads(path.read_text(encoding='utf-8'))
    approval = json.loads(Path(approval_path).read_text(encoding='utf-8-sig'))
    known = validate(session, approval)
    cache = Cache(db_path)
    setup(cache)
    cache.db.execute('''CREATE TABLE IF NOT EXISTS title_reviews(
        session TEXT, thread TEXT, state TEXT, decision TEXT, receipt TEXT,
        PRIMARY KEY(session,thread))''')
    client = rpc = None
    receipts = []
    original_state = session['state']
    try:
        for d in approval['decisions']:
            tid = d['thread']; row = known[tid]
            old = cache.db.execute('SELECT * FROM title_reviews WHERE session=? AND thread=?', (sid,tid)).fetchone()
            if old:
                if old['decision'] != encoded(d):
                    raise ValueError('Already attempted title cannot be changed/replayed in this session')
                receipt = json.loads(old['receipt']) if old['receipt'] else {'thread':tid,'state':'needs_review'}
                if old['state'] == 'verified':
                    row.update(receipt['result'])
                receipts.append({**receipt, 'skipped_already_attempted': True})
                continue
            if cache.db.execute('SELECT 1 FROM locks WHERE thread=?', (tid,)).fetchone():
                raise ValueError('Chat protected by another operation')
            if client is None:
                client = DesktopMcp(allow_title=True)
                rpc = Rpc()
            baseline = row['before']
            current = inventory(client, [tid]).get(tid)
            fields = ('title', 'projectId', 'cwd', 'pinnedIndex', 'updatedAt')
            if not current or current.get('status') == 'active' or any(current.get(k) != baseline.get(k) for k in fields):
                receipts.append({'thread':tid,'state':'external_change'})
                continue
            fresh = read_snapshot(rpc, tid)
            current = inventory(client, [tid]).get(tid)
            if fingerprint(fresh['messages']) != row['content_hash'] or not current or current.get('status') == 'active' or any(current.get(k) != baseline.get(k) for k in fields):
                receipts.append({'thread':tid,'state':'content_or_metadata_changed'})
                continue
            with cache.db:
                cache.db.execute('INSERT INTO title_reviews VALUES(?,?,?, ?,NULL)', (sid,tid,'attempted',encoded(d)))
                cache.db.execute('INSERT INTO locks VALUES(?,?)', (tid,'标题确认后进入执行；禁止重复分析及自动重放'))
            receipt = {'thread':tid,'state':'needs_review'}
            try:
                target = d['title']
                if target != baseline['title']:
                    raw = client.request('tools/call', {'name':'set_thread_title',
                          'arguments':{'threadId':tid,'source':'codex','title':target}})
                    receipt['tool_response'] = raw
                after = inventory(client, [tid]).get(tid)
                verified = bool(after and after['title'] == target and all(
                    after.get(k) == baseline.get(k) for k in ('projectId','cwd','pinnedIndex')))
                if verified:
                    result = dict(state='desktop_state_verified', after_title=after['title'],
                                  after_project=after.get('projectId'), after_cwd=after.get('cwd'),
                                  after_updated_at=after.get('updatedAt'), visual_verified=False)
                    row.update(result)
                    receipt.update(state='verified', result=result)
            except Exception as exc:
                receipt['error'] = str(exc)
            with cache.db:
                cache.db.execute('UPDATE title_reviews SET state=?,receipt=? WHERE session=? AND thread=?',
                                 (receipt['state'],encoded(receipt),sid,tid))
            receipts.append(receipt)
            session['title_receipts'] = receipts
            session['state'] = 'title_partial'
            save(path, session)
            if receipt['state'] != 'verified':
                break
        complete = all(r.get('state') == 'desktop_state_verified' for r in known.values())
        session['state'] = ('awaiting_group_review' if complete else 'title_partial')
        if original_state in ('grouping_partial','grouping_completed'):
            session['state'] = original_state
        session['title_receipts'] = receipts
        outputs(session, session['projects'])
        save(path, session)
        return session
    finally:
        cache.db.close()
        if client: client.close()
        if rpc: rpc.close()
