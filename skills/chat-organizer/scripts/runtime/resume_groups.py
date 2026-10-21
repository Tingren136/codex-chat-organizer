"""Resume only user-approved moves after desktop project registration."""
import argparse
import json
from pathlib import Path
import sqlite3

from batch_cache import ROOT
from desktop_mcp_probe import DesktopMcp
from project_executor import call, inventory, restore_pin
from review_workflow import rows, validate_approval
from offline_registration import load, save

FIELDS = ('title', 'projectId', 'cwd', 'pinnedIndex')


def move_pending(plan, client, db, project_ids):
    if any(item['target'] not in project_ids for item in plan['pending']):
        raise ValueError('Desktop project registration still missing; no moves attempted')
    db.execute('CREATE TABLE IF NOT EXISTS registration_repair_moves('
               'plan TEXT,thread TEXT,state TEXT,receipt TEXT,PRIMARY KEY(plan,thread))')
    result = []
    ids = [item['thread'] for item in plan['pending']]
    initial = inventory(client, ids)
    for item in plan['pending']:
        tid, target, expected = item['thread'], item['target'], item['expected']
        old = db.execute('SELECT state,receipt FROM registration_repair_moves WHERE plan=? AND thread=?',
                         (plan['id'], tid)).fetchone()
        if old:
            result.append({'thread': tid, 'state': old[0], 'skipped_existing_attempt': True})
            continue
        before = inventory(client, ids)[tid]
        if before.get('projectId') == target:
            if any(before.get(k) != expected.get(k) for k in ('title', 'cwd', 'pinnedIndex')):
                raise ValueError('Chat changed after report: ' + tid)
            result.append({'thread': tid, 'state': 'already_at_target'})
            continue
        if before.get('status') == 'active' or any(before.get(k) != expected.get(k)
                 for k in FIELDS + ('updatedAt',)):
            raise ValueError('Chat changed or became active; preserve it: ' + tid)
        receipt = {'thread': tid, 'target': target, 'before': before, 'state': 'attempted'}
        with db:
            db.execute('INSERT INTO registration_repair_moves VALUES(?,?,?,?)',
                       (plan['id'], tid, 'attempted', json.dumps(receipt, ensure_ascii=False)))
        try:
            response = client.request('tools/call', {'name': 'move_thread_to_project', 'arguments': {
                'threadId': tid, 'hostId': 'local', 'source': 'codex', 'projectId': target}})
            after = inventory(client, ids)[tid]
            if after.get('projectId') == target and before.get('pinnedIndex') is not None:
                receipt['pin_restore'] = restore_pin(db, client, tid, before, initial)
                after = inventory(client, ids)[tid]
            verified = after.get('projectId') == target and all(after.get(k) == before.get(k)
                                                                for k in ('title', 'cwd', 'pinnedIndex'))
            receipt.update(tool_response=response, after=after, state='verified' if verified else 'needs_review')
        except Exception as exc:
            receipt.update(state='needs_review', error=str(exc))
        with db:
            db.execute('UPDATE registration_repair_moves SET state=?,receipt=? WHERE plan=? AND thread=?',
                       (receipt['state'], json.dumps(receipt, ensure_ascii=False), plan['id'], tid))
        result.append(receipt)
        if receipt['state'] != 'verified':
            break
    return result


def execute(plan_path):
    plan_path = Path(plan_path)
    plan = load(plan_path)
    session_file = ROOT / 'work/review-sessions' / plan['session_id'] / 'session.json'
    session = load(session_file)
    approval = load(Path(plan['approval_path']))
    validate_approval(session, approval)
    if plan['review_digest'] != approval['review_digest'] or len(plan['pending']) > 20:
        raise ValueError('Plan/report mismatch')
    if len({item['thread'] for item in plan['pending']}) != len(plan['pending']):
        raise ValueError('Duplicate pending chat')
    names = {p['name']: p['id'] for p in plan['projects']}
    decisions = {d['thread']: d['destination'] for d in approval['decisions']}
    for item in plan['pending']:
        if item['thread'] != session['selected'][item['number'] - 1]:
            raise ValueError('Chat identity mismatch')
        approved = decisions[item['thread']]
        if isinstance(approved, str) and approved.startswith('new:'):
            approved = names.get(approved[4:])
        if approved != item['target'] or item['thread'] in session['excluded_before']:
            raise ValueError('Target differs from user-approved plan')
    worker_status = load(plan_path.parent / 'worker-status.json')
    if worker_status['state'] not in ('applied_config_verified', 'already_applied'):
        raise ValueError('Offline registration has not completed')
    # Shared lock blocks concurrent scans or other grouping runs.
    import msvcrt
    lock = (ROOT / 'work/oneclick.lock').open('a+b')
    lock.seek(0)
    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    client = None
    db = sqlite3.connect(ROOT / 'work/organizer-cache.sqlite')
    report = {'state': 'checking', 'model_calls': 0, 'screen_actions': 0}
    report_path = plan_path.parent / 'grouping-result.json'
    try:
        client = DesktopMcp(allow_project_move=True, allow_pin_restore=True)
        catalog = call(client, 'list_projects', {})['projects']
        project_ids = {p['projectId'] for p in catalog if p.get('projectKind') == 'local'}
        all_ids = list(plan['baseline'])
        pending_ids = {i['thread'] for i in plan['pending']}
        before = inventory(client, all_ids)
        protected = {tid: old for tid, old in plan['baseline'].items() if tid not in pending_ids}
        if not all(all(before[tid].get(k) == old.get(k) for k in FIELDS) for tid, old in protected.items()):
            raise ValueError('A completed chat changed externally; preserve current state')
        model_before = db.execute('SELECT count(*) FROM model_jobs').fetchone()[0]
        report['moves'] = move_pending(plan, client, db, project_ids)
        after = inventory(client, all_ids)
        report['protected_unchanged'] = all(all(after[tid].get(k) == old.get(k) for k in FIELDS)
                                          for tid, old in protected.items())
        report['protected_count'] = len(protected)
        report['verified_pending'] = {i['thread']: after[i['thread']].get('projectId') == i['target']
                    and all(after[i['thread']].get(k) == i['expected'].get(k)
                            for k in ('title', 'cwd', 'pinnedIndex')) for i in plan['pending']}
        report['after'] = {tid: {k: after[tid].get(k) for k in FIELDS} for tid in all_ids}
        report['model_jobs_before'] = model_before
        report['model_jobs_after'] = db.execute('SELECT count(*) FROM model_jobs').fetchone()[0]
        if report['model_jobs_after'] != model_before or not report['protected_unchanged']:
            raise RuntimeError('Protection or model-count check failed')
        report['state'] = 'approved_moves_verified' if all(report['verified_pending'].values()) else 'partial_needs_review'
        # Preserve old failed receipts. A separate repair receipt records this authorized new attempt.
        session['registration_repair_receipt'] = str(report_path)
        if report['state'] == 'approved_moves_verified':
            named = {r['thread']: r for r in rows(session)}
            audit = {}
            for tid, target in decisions.items():
                if target == 'keep':
                    target = named[tid]['after_project']
                elif isinstance(target, str) and target.startswith('new:'):
                    target = names[target[4:]]
                audit[tid] = after[tid].get('projectId') == target
            report['all_approved_membership_verified'] = audit
            if all(audit.values()) and len(decisions) == len(rows(session)):
                session['state'] = 'grouping_completed'
        save(session_file, session)
    except Exception as exc:
        report.update(state='stopped_needs_review', error=str(exc))
        raise
    finally:
        save(report_path, report)
        if client:
            client.close()
        db.close()
        lock.close()
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    arguments = parser.parse_args()
    result = execute(arguments.plan)
    print(json.dumps({'state': result['state'], 'verified_pending': result.get('verified_pending'),
                      'protected_unchanged': result.get('protected_unchanged')}, ensure_ascii=False))
