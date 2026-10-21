"""Approved project creation and narrowly scoped desktop repair preparation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess

from batch_cache import ROOT
from desktop_mcp_probe import DesktopMcp
from offline_registration import load, save, core_rows, patched_state
from probe import Rpc
from project_executor import call, inventory
from review_workflow import session_path, validate_approval, rows
from settings import absolute, codex_home, desktop_server

VALIDATED_OFFLINE_BUILD = '26.1013.4894.0'


def project_list(rpc):
    result, cursor = [], None
    for _ in range(100):
        page = rpc.call('project/list', {'limit': 100, 'cursor': cursor})
        result.extend(page['data'])
        cursor = page.get('nextCursor')
        if not cursor:
            return result
    raise RuntimeError('Project pagination cap reached')


def ensure_project(rpc, name, roots, journal_path, key):
    roots = [str(absolute(path)) for path in roots]
    if not name.strip() or not roots or len(roots) != len(set(roots)):
        raise ValueError('Unique absolute roots and a nonempty project name are required')
    journal_path = Path(journal_path)
    old = load(journal_path) if journal_path.exists() else None
    params = {'name': name, 'roots': [{'path': p} for p in roots], 'idempotencyKey': key}
    if old and old['params'] != params:
        raise ValueError('Do not replace an existing creation intent')
    if old and old.get('project'):
        project = rpc.call('project/read', {'projectId': old['project']['id']})['project']
        if project['name'] != name or [p['path'] for p in project['roots']] != roots:
            raise ValueError('Project changed after creation; preserve it')
        return project
    matches = [p for p in project_list(rpc) if p['name'] == name]
    if matches:
        if len(matches) != 1 or [p['path'] for p in matches[0]['roots']] != roots:
            raise ValueError('Same-name project has different roots; do not create a duplicate')
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        save(journal_path, {'state': 'reused', 'params': params, 'project': matches[0]})
        return matches[0]
    if old:
        raise RuntimeError('Previous creation was uncertain; inspect it rather than retry')
    if not all(Path(path).is_dir() for path in roots):
        raise ValueError('Create/choose approved project folders before calling this command')
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    intent = {'state': 'attempted', 'params': params}
    save(journal_path, intent)
    result = rpc.call('project/create', params)['project']
    readback = rpc.call('project/read', {'projectId': result['id']})['project']
    if readback != result:
        raise RuntimeError('Project creation readback mismatch')
    save(journal_path, {**intent, 'state': 'backend_verified', 'project': readback})
    return readback


def prepare(sid, approval_path, roots_path, repair_confirmation=''):
    session = load(session_path(sid))
    approval_path = Path(approval_path).resolve()
    approval = load(approval_path)
    validate_approval(session, approval)
    roots = load(roots_path)
    requested_names = {d['destination'][4:] for d in approval['decisions']
                       if isinstance(d['destination'], str) and d['destination'].startswith('new:')}
    if set(roots) != requested_names:
        raise ValueError('Roots must describe exactly the approved new project names')
    directory = ROOT / 'work' / 'project-management' / sid
    directory.mkdir(parents=True, exist_ok=True)
    plan_path = directory / 'plan.json'
    if plan_path.exists():
        prior = load(plan_path)
        if prior['review_digest'] != approval['review_digest'] or prior['approval_path'] != str(approval_path):
            raise ValueError('Existing management plan belongs to different approval')
        return {'state': 'existing_plan_no_replay', 'plan': str(plan_path)}
    rpc, client = Rpc(), None
    try:
        created = []
        for name in sorted(requested_names):
            key = hashlib.sha256(json.dumps([sid, name, roots[name]], ensure_ascii=False).encode()).hexdigest()
            created.append(ensure_project(rpc, name, roots[name], directory / (key + '.json'), key))
        client = DesktopMcp()
        catalog = call(client, 'list_projects', {})['projects']
        native_ids = {p['projectId'] for p in catalog if p.get('projectKind') == 'local'}
        name_ids = {p['name']: p['id'] for p in created}
        db = sqlite3.connect((ROOT / 'work/organizer-cache.sqlite').as_uri() + '?mode=ro', uri=True)
        try:
            locked = {row[0] for row in db.execute('SELECT thread FROM locks')}
        finally:
            db.close()
        ids = sorted(locked | set(session['selected']))
        before = inventory(client, ids)
        state = load(codex_home() / '.codex-global-state.json')
        named = {r['thread']: r for r in rows(session)}
        pending = []
        for decision in approval['decisions']:
            tid, target = decision['thread'], decision['destination']
            if target == 'keep':
                continue
            if isinstance(target, str) and target.startswith('new:'):
                target = name_ids[target[4:]]
            if target is None:
                raise ValueError('Use the normal apply command for explicit project removal')
            if target not in native_ids | set(name_ids.values()):
                raise ValueError('Destination is absent from approved local project catalog')
            current = before[tid]
            if current.get('projectId') == target:
                continue
            if current.get('status') == 'active' or current['title'] != named[tid]['after_title'] or current.get('projectId') != named[tid]['after_project']:
                raise ValueError('Chat changed since approved report; preserve it')
            if named[tid].get('after_updated_at') is not None and current.get('updatedAt') != named[tid]['after_updated_at']:
                raise ValueError('Chat content changed since approved report')
            pending.append({'number': session['selected'].index(tid) + 1, 'thread': tid,
                            'target': target, 'expected': {k: current.get(k) for k in
                            ('title', 'projectId', 'cwd', 'pinnedIndex', 'updatedAt', 'status')}})
        baseline = {tid: {k: before[tid].get(k) for k in ('title', 'projectId', 'cwd', 'pinnedIndex')}
                    for tid in ids}
        missing = [p for p in created if p['id'] not in native_ids]
        # Source repairs are an explicit maintenance operation, not the default naming stage.
        orphans = [i['thread'] for i in pending if i['expected']['projectId'] is None
                   and not state.get('thread-project-assignments', {}).get(i['thread'])
                   and i['thread'] not in state.get('projectless-thread-ids', [])]
        offline = bool(missing or orphans)
        server = desktop_server()
        core_db = codex_home() / 'state_5.sqlite'
        core_before = {}
        if offline:
            if not repair_confirmation.strip():
                raise ValueError('Backend projects saved; desktop repair requires explicit user instruction. Reuse creation journals.')
            if VALIDATED_OFFLINE_BUILD not in str(server):
                raise ValueError('Offline patch not validated for this installed build; stop for version review')
            core_before = core_rows(core_db, ids)
            for tid in orphans:
                meta = core_before[tid]
                if meta['project_id'] is not None or meta['archived'] or meta['source'] != 'vscode' or not Path(before[tid]['cwd']).is_dir():
                    raise ValueError('Insufficient evidence to normalize source: ' + tid)
        plan = {'id': 'approved-projects-' + sid, 'session_id': sid,
                'review_digest': approval['review_digest'], 'approval_path': str(approval_path),
                'user_confirmation': approval['user_confirmation'], 'repair_confirmation': repair_confirmation,
                'state_path': str(codex_home() / '.codex-global-state.json'), 'core_db': str(core_db),
                'app_exe': str(server.parents[5] / 'ChatGPT.exe'),
                'projects': [{'id': p['id'], 'name': p['name'], 'rootPaths': [r['path'] for r in p['roots']],
                              'createdAt': p['createdAt'] * 1000, 'updatedAt': p['updatedAt'] * 1000} for p in created],
                'orphans': orphans, 'expected_assignment': None, 'pending': pending,
                'baseline': baseline, 'core_before': core_before, 'offline_required': offline}
        # Only missing definitions may enter the offline patch; existing project records stay untouched.
        plan['registration_projects'] = [p for p in plan['projects'] if p['id'] not in native_ids]
        patch_plan = {**plan, 'projects': plan['registration_projects']}
        if offline:
            patched_state(state, patch_plan)  # copy-only structure/conflict validation
        save(plan_path, plan)
        save(directory / 'patch-plan.json', patch_plan)
        if not offline:
            save(directory / 'worker-status.json', {'state': 'already_applied', 'offline_required': False})
        return {'state': 'waiting_for_offline_repair' if offline else 'ready_to_move',
                'plan': str(plan_path), 'patch_plan': str(directory / 'patch-plan.json'),
                'pending': len(pending), 'backend_projects': len(created), 'model_calls': 0}
    finally:
        rpc.close()
        if client:
            client.close()


def arm(patch_plan_path):
    patch_plan_path = Path(patch_plan_path).resolve()
    plan = load(patch_plan_path)
    if not plan.get('offline_required') or not plan.get('repair_confirmation'):
        raise ValueError('This plan does not authorize an offline repair')
    status_path = patch_plan_path.parent / 'worker-status.json'
    if status_path.exists():
        status = load(status_path)
        if status['state'] not in ('expired_no_write', 'cancelled'):
            raise ValueError('Existing worker/repair status must be checked before arming again')
    pythonw = Path(os.sys.executable).with_name('pythonw.exe')
    if not pythonw.is_file():
        raise RuntimeError('A matching pythonw.exe is required for the independent helper')
    command = subprocess.list2cmdline([str(pythonw), '-X', 'utf8', str(Path(__file__).with_name('offline_registration.py')),
                                      'wait', '--plan', str(patch_plan_path)])
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    ps = ("$startup=New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ShowWindow=[uint16]0}; "
          "Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=" + quote(command) +
          ";CurrentDirectory=" + quote(patch_plan_path.parent) + ";ProcessStartupInformation=$startup} | "
          "Select-Object ProcessId,ReturnValue | ConvertTo-Json -Compress")
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', ps],
                            capture_output=True, text=True, encoding='utf-8', timeout=20,
                            creationflags=subprocess.CREATE_NO_WINDOW, check=True)
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare-projects')
    p.add_argument('--session-id', required=True)
    p.add_argument('--approval', type=Path, required=True)
    p.add_argument('--roots', type=Path, required=True)
    p.add_argument('--repair-confirmation', default='')
    p = sub.add_parser('arm-repair'); p.add_argument('--plan', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'arm-repair':
        result = arm(args.plan)
    else:
        import msvcrt
        (ROOT / 'work').mkdir(parents=True, exist_ok=True)
        with (ROOT / 'work/oneclick.lock').open('a+b') as lock:
            if lock.tell() == 0:
                lock.write(b'0'); lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            result = prepare(args.session_id, args.approval, args.roots, args.repair_confirmation)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
