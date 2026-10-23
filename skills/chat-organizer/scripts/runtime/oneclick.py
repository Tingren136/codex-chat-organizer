"""One command: exclude processed, read, one model batch, native desktop writes.

Requires the existing Codex desktop executor environment. No screen automation.
Reusing --run-id returns the stored report without reconnecting or spending tokens.
"""
import argparse
import json
import os
from pathlib import Path
import uuid
import msvcrt

from batch_cache import Cache, ROOT, encoded, fingerprint
from desktop_mcp_probe import DesktopMcp
from pipeline import initialize, analyze
from naming_policy import load_policy
from probe import Rpc
from project_executor import call, inventory, restore_pin

DB = ROOT / 'work' / 'organizer-cache.sqlite'


def setup(cache):
    initialize(cache)
    cache.db.executescript('''
      CREATE TABLE IF NOT EXISTS unified_runs(
        id TEXT PRIMARY KEY, state TEXT NOT NULL, report TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS unified_actions(
        thread TEXT, stage TEXT, run TEXT, state TEXT, receipt TEXT,
        PRIMARY KEY(thread,stage));
      CREATE TABLE IF NOT EXISTS deferred_reads(
        thread TEXT PRIMARY KEY, reason TEXT NOT NULL, characters INTEGER);
    ''')


def excluded(cache):
    ids = {r[0] for r in cache.db.execute('SELECT thread FROM locks')}
    ids |= {r[0] for r in cache.db.execute('SELECT DISTINCT thread FROM snapshots')}
    ids |= {r[0] for r in cache.db.execute('SELECT thread FROM unified_actions')}
    ids |= {r[0] for r in cache.db.execute('SELECT thread FROM deferred_reads')}
    return ids | {os.environ.get('CODEX_THREAD_ID')}


def select(cache, threads, ids, limit):
    deny = excluded(cache)
    pool = [threads[i] for i in ids if i in threads] if ids else list(threads.values())
    return [t for t in pool if t['id'] not in deny and t.get('kind') == 'codex'
            and t.get('hostId') == 'local' and t.get('status') in ('idle', 'notLoaded')][:limit]


def read_snapshot(rpc, tid):
    first = rpc.call('thread/read', {'threadId': tid, 'includeTurns': False})['thread']
    messages, cursor, turns, cursors = [], None, set(), set()
    for _ in range(100):
        params = {'threadId': tid, 'limit': 20, 'sortDirection': 'asc', 'itemsView': 'full'}
        if cursor:
            params['cursor'] = cursor
        page = rpc.call('thread/turns/list', params)
        for turn in page['data']:
            if turn['id'] in turns or turn.get('itemsView') not in (None, 'full'):
                raise ValueError('Duplicate/partial history')
            turns.add(turn['id'])
            for item in turn.get('items', []):
                if item.get('type') in ('userMessage', 'agentMessage'):
                    messages.append({'turn': turn['id'], 'type': item['type'],
                                     'text': item.get('text'), 'content': item.get('content')})
        cursor = page.get('nextCursor')
        if not cursor:
            break
        if cursor in cursors:
            raise ValueError('Repeated cursor')
        cursors.add(cursor)
    else:
        raise ValueError('History read cap reached')
    last = rpc.call('thread/read', {'threadId': tid, 'includeTurns': False})['thread']
    if any(first.get(k) != last.get(k) for k in ('name', 'cwd', 'updatedAt')):
        raise ValueError('Chat changed during read')
    return {'meta': {k: last.get(k) for k in ('id', 'name', 'cwd', 'projectId', 'updatedAt')},
            'messages': messages, 'complete': True, 'turn_count': len(turns)}


def persist(cache, report, state):
    report['state'] = state
    with cache.db:
        cache.db.execute('INSERT OR REPLACE INTO unified_runs VALUES(?,?,?)',
                         (report['run'], state, encoded(report)))
    path = ROOT / 'work' / ('run-' + report['run'] + '.json')
    path.write_text(encoded(report), encoding='utf-8')


def action(cache, client, run_id, tid, stage, tool, arguments):
    # Unique intent makes ambiguous failures non-retryable.
    with cache.db:
        cache.db.execute('INSERT INTO unified_actions VALUES(?,?,?,\'attempted\',NULL)',
                         (tid, stage, run_id))
    response = client.request('tools/call', {'name': tool, 'arguments': arguments})
    with cache.db:
        cache.db.execute('UPDATE unified_actions SET state=?,receipt=? WHERE thread=? AND stage=?',
                         ('error' if response.get('isError') else 'returned', encoded(response), tid, stage))
    if response.get('isError'):
        raise RuntimeError('Official tool error, no retry: ' + encoded(response))
    return response


def run(run_id, ids=None, limit=2, naming_only=True, proposed_groups=None, prepared_snapshots=None, naming_policy=None):
    if not 1 <= limit <= 2 or (ids and len(ids) > 50):
        raise ValueError('This rollout is capped at two new chats')
    if not naming_only:
        raise ValueError('Automatic project moves are disabled; use reviewed session approvals')
    policy = naming_policy or load_policy()
    cache = Cache(DB)
    setup(cache)
    previous = cache.db.execute('SELECT report FROM unified_runs WHERE id=?', (run_id,)).fetchone()
    if previous:
        cache.db.close()
        return {'skipped_existing_run': True, 'stored_report': json.loads(previous[0])}
    report = {'run': run_id, 'model_calls': 0, 'screen_actions': 0, 'items': [], 'skipped': [], 'deferred': []}
    client = rpc = None
    try:
        if ids and all(i in excluded(cache) for i in ids):
            report['skipped'] = [{'thread': i, 'reason': 'already_processed'} for i in ids]
            persist(cache, report, 'no_new_work')
            return report
        persist(cache, report, 'started')
        client = DesktopMcp()
        initial = inventory(client, ids or ())
        protected = {k: t for k, t in initial.items() if k in excluded(cache)}
        projects = [p for p in call(client, 'list_projects', {})['projects']
                    if p.get('projectKind') == 'local' and p.get('hostId') == 'local']
        selected = select(cache, initial, ids, limit)
        report['skipped'] = [{'thread': i, 'reason': 'processed_missing_or_active'}
                             for i in (ids or []) if i not in {t['id'] for t in selected}]
        if not selected:
            persist(cache, report, 'no_new_work')
            return report
        rpc = Rpc()
        snapshots = {}
        for thread in selected:
            snap = (prepared_snapshots or {}).get(thread['id'])
            if not snap or snap['meta'].get('updatedAt') != thread.get('updatedAt'):
                snap = read_snapshot(rpc, thread['id'])
            if snap['meta']['name'] is None:
                snap['meta']['name'] = thread['title']
            if snap['meta']['name'] != thread['title'] or snap['meta']['cwd'] != thread.get('cwd'):
                raise ValueError('Desktop/backend metadata conflict')
            # Bound first-pass spend; never truncate history into a false full view.
            characters = len(encoded(snap['messages']))
            if characters > 100000:
                with cache.db:
                    cache.db.execute('INSERT OR IGNORE INTO deferred_reads VALUES(?,?,?)',
                                     (thread['id'], 'long_history', characters))
                report['deferred'].append({'thread':thread['id'], 'title':thread['title'],
                                          'reason':'long_history', 'characters':characters})
                continue
            if sum(len(encoded(s['messages'])) for s in snapshots.values()) + characters > 139900:
                report['skipped'].append({'thread':thread['id'], 'reason':'batch_capacity_not_attempted'})
                continue
            snapshots[thread['id']] = snap
        if not snapshots:
            persist(cache, report, 'completed')
            return report
        if len(encoded([s['messages'] for s in snapshots.values()])) > 140000:
            raise ValueError('Batch exceeds initial spend cap')
        if cache.db.execute("SELECT 1 FROM snapshots WHERE state='pending'").fetchone():
            raise ValueError('Unrelated pending cache entries need review')
        for snap in snapshots.values():
            cache.ingest(snap)
        batch = cache.export(limit)
        batch['naming_policy'] = policy
        batch['desktop'] = {'projects': [{'id': p['projectId'], 'name': p['label']} for p in projects],
                            'assignments': {t['id']: t.get('projectId') for t in selected}}
        if naming_only:
            batch['grouping_review'] = True
            batch['proposed_groups'] = proposed_groups or []
        batch_id = batch['batch']
        (ROOT / 'work' / ('batch-' + batch_id + '.json')).write_text(encoded(batch), encoding='utf-8')
        report['batch'] = batch_id
        persist(cache, report, 'analyzing')
        result = analyze(cache, batch_id)
        report['model_calls'] = 0 if result.get('skipped') else 1
        report['model_receipt'] = result
        if result.get('state') != 'completed':
            raise RuntimeError('Model did not complete; no automatic resend')
        persist(cache, report, 'preparing_review')
        results = json.loads(Path(result['output']).read_text(encoding='utf-8'))['items']
        project_ids = {p['projectId'] for p in projects}
        for decision in results:
            tid = decision['thread']
            before = initial[tid]
            receipt = {'thread': tid, 'before_title': before['title'], 'before_project': before.get('projectId'),
                       'suggested_title': decision['title'], 'suggested_project': decision['project_id'],
                       'summary': decision['summary'], 'reason': decision['reason'],
                       'group_action': decision.get('group_action'),
                       'proposed_project_name': decision.get('proposed_project_name'),
                       'group_reason': decision.get('group_reason'),
                       'project_stage': 'awaiting_user_review' if naming_only else 'automatic'}
            report['items'].append(receipt)
            receipt.update(state='awaiting_title_review', before=dict(before),
                           content_hash=decision['hash'], title_action=decision['title_action'],
                           naming_policy=policy)
            persist(cache, report, 'preparing_review')
        final = inventory(client, list(protected))
        checks = {tid: bool(tid in final and all(old.get(k) == final[tid].get(k)
                  for k in ('title', 'projectId', 'cwd', 'pinnedIndex'))) for tid, old in protected.items()}
        report['protected_unchanged'] = checks
        if not all(checks.values()):
            raise RuntimeError('Protected item changed externally; review only')
        persist(cache, report, 'completed')
        return report
    except Exception as exc:
        report['error'] = str(exc)
        persist(cache, report, 'needs_review')
        raise
    finally:
        if client:
            client.close()
        if rpc:
            rpc.close()
        cache.db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', default=None)
    parser.add_argument('--ids', nargs='+')
    parser.add_argument('--limit', type=int, default=2)
    args = parser.parse_args()
    run_id = str(uuid.UUID(args.run_id)) if args.run_id else str(uuid.uuid4())
    lock = (ROOT / 'work' / 'oneclick.lock').open('a+b')
    try:
        lock.seek(0)
        if lock.read(1) == b'':
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        print(encoded(run(run_id, args.ids, args.limit)))
    finally:
        lock.close()


if __name__ == '__main__':
    main()
