"""Narrow, offline-only registration repair. Never creates projects or edits DBs."""
import argparse
import copy
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.new')
    with temp.open('w', encoding='utf-8') as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp, path)


def patched_state(state, plan):
    result = copy.deepcopy(state)
    projects = result.setdefault('local-projects', {})
    order = result.setdefault('project-order', [])
    for project in plan['projects']:
        pid = project['id']
        if pid in projects and projects[pid] != project:
            raise ValueError('Project changed: ' + pid)
        if any(p.get('name') == project['name'] and key != pid for key, p in projects.items()):
            raise ValueError('Duplicate project name: ' + project['name'])
        projects[pid] = copy.deepcopy(project)
        if pid not in order:
            order.append(pid)
    projectless = result.setdefault('projectless-thread-ids', [])
    for tid in plan.get('orphans', [plan['orphan']] if plan.get('orphan') else []):
        if result.get('thread-project-assignments', {}).get(tid) != plan['expected_assignment']:
            raise ValueError('Orphan now has a different assignment; preserve it: ' + tid)
        if tid not in projectless:
            projectless.append(tid)
    allowed = {'local-projects', 'project-order', 'projectless-thread-ids'}
    assert all(result.get(k) == state.get(k) for k in set(state) | set(result) if k not in allowed)
    return result


def reverted_state(state, original, plan):
    expected = patched_state(original, plan)
    result = copy.deepcopy(state)
    new_ids = {p['id'] for p in plan['projects']} - set(original.get('local-projects', {}))
    if any(a.get('projectId') in new_ids for a in state.get('thread-project-assignments', {}).values()):
        raise ValueError('Projects already have members; first undo their recorded moves')
    for pid in new_ids:
        if result.get('local-projects', {}).get(pid) != expected['local-projects'][pid]:
            raise ValueError('Registered project changed after repair: ' + pid)
        del result['local-projects'][pid]
    result['project-order'] = [pid for pid in result.get('project-order', []) if pid not in new_ids]
    new_orphans = set(plan.get('orphans', [plan['orphan']] if plan.get('orphan') else [])) - set(original.get('projectless-thread-ids', []))
    result['projectless-thread-ids'] = [tid for tid in result.get('projectless-thread-ids', [])
                                      if tid not in new_orphans]
    return result


def write_patch(state_path, plan, backup_dir, app_guard):
    state_path, backup_dir = Path(state_path), Path(backup_dir)
    if app_guard():
        raise RuntimeError('Client is running; no write permitted')
    raw = state_path.read_bytes()
    original = json.loads(raw)
    after = patched_state(original, plan)
    if after == original:
        return {'state': 'already_applied', 'after': after, 'sha256': sha(raw)}
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / 'before.json'
    if backup.exists() and backup.read_bytes() != raw:
        raise RuntimeError('Existing backup differs; review previous attempt')
    if not backup.exists():
        with backup.open('xb') as file:
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
    assert backup.read_bytes() == raw
    save(backup_dir / 'intent.json', {'before_sha256': sha(raw), 'plan': plan,
                                    'changed_keys': ['local-projects', 'project-order', 'projectless-thread-ids']})
    if app_guard():
        raise RuntimeError('Client reopened; no write permitted')
    if state_path.read_bytes() != raw:
        raise RuntimeError('State changed during preparation; no write permitted')
    # All unrelated values came from the latest file, never an old configuration.
    save(state_path, after)
    actual = load(state_path)
    if actual != after:
        raise RuntimeError('Readback mismatch')
    receipt = {'state': 'applied_config_verified', 'before_sha256': sha(raw),
               'after_sha256': sha(state_path.read_bytes()), 'chat_moves': 0,
               'registered_ids': [p['id'] for p in plan['projects']],
               'orphans': plan.get('orphans', [plan['orphan']] if plan.get('orphan') else [])}
    save(backup_dir / 'receipt.json', receipt)
    return {**receipt, 'after': after}


def running_desktops():
    """Conservatively gate on every ChatGPT.exe in this Windows session."""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    class Entry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                    ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                    ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                    ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
                    ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    own_session = wintypes.DWORD()
    if not kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(own_session)):
        raise ctypes.WinError(ctypes.get_last_error())
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    entry = Entry()
    entry.dwSize = ctypes.sizeof(entry)
    pids = []
    try:
        ok = kernel.Process32FirstW(handle, ctypes.byref(entry))
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        while ok:
            if entry.szExeFile.lower() == 'chatgpt.exe':
                session = wintypes.DWORD()
                if not kernel.ProcessIdToSessionId(entry.th32ProcessID, ctypes.byref(session)):
                    raise ctypes.WinError(ctypes.get_last_error())
                if session.value == own_session.value:
                    pids.append(entry.th32ProcessID)
            ok = kernel.Process32NextW(handle, ctypes.byref(entry))
        if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(handle)
    return pids


def core_rows(db_path, ids):
    connection = sqlite3.connect(Path(db_path).as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        result = {}
        for tid in ids:
            row = connection.execute('SELECT id,name,cwd,project_id,source,archived,updated_at,is_pinned '
                                     'FROM threads WHERE id=?', (tid,)).fetchone()
            if row is None:
                raise ValueError('Thread missing: ' + tid)
            result[tid] = dict(row)
        return result
    finally:
        connection.close()


def worker(plan_path):
    import msvcrt
    plan_path = Path(plan_path)
    plan = load(plan_path)
    folder = plan_path.parent
    lock = (folder / 'worker.lock').open('a+b')
    if lock.tell() == 0:
        lock.write(b'0'); lock.flush()
    lock.seek(0)
    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    status_path = folder / 'worker-status.json'
    status = {'pid': os.getpid(), 'parent_pid': os.getppid(), 'state': 'waiting_for_exit',
              'started_at': time.time(), 'deadline': time.time() + 1800,
              'model_calls': 0, 'screen_actions': 0, 'chat_moves': 0}
    stable = 0
    try:
        while time.time() < status['deadline']:
            if (folder / 'cancel').exists():
                status['state'] = 'cancelled'; break
            pids = running_desktops()
            stable = 0 if pids else stable + 1
            status.update(heartbeat=time.time(), desktop_count=len(pids), offline_samples=stable)
            save(status_path, status)
            if stable >= 4:
                # Recheck targeted metadata only; never read chat content or write SQLite.
                if core_rows(plan['core_db'], plan['core_before']) != plan['core_before']:
                    raise ValueError('Protected or pending metadata changed since plan; stopped')
                status['state'] = 'writing_offline_patch'; save(status_path, status)
                result = write_patch(plan['state_path'], plan, folder / 'backup', running_desktops)
                status.update(state=result['state'], config_after_sha256=result['sha256']
                              if 'sha256' in result else result['after_sha256'])
                save(status_path, status)
                # Reopening the app is the only UI-related action; no keyboard/mouse automation.
                if not running_desktops():
                    subprocess.Popen([plan['app_exe']], stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                                     cwd=str(folder))
                    status['reopen_requested'] = True
                break
            time.sleep(2)
        else:
            status['state'] = 'expired_no_write'
    except Exception as exc:
        status.update(state='stopped_needs_review', error=str(exc))
    finally:
        status['heartbeat'] = time.time()
        save(status_path, status)
        lock.close()


def rollback(plan_path):
    plan_path = Path(plan_path)
    plan = load(plan_path)
    if running_desktops():
        raise RuntimeError('Exit the client before rollback')
    folder = plan_path.parent / 'backup'
    receipt = load(folder / 'receipt.json')
    state_path = Path(plan['state_path'])
    raw = state_path.read_bytes()
    original = load(folder / 'before.json')
    if sha((folder / 'before.json').read_bytes()) != receipt['before_sha256']:
        raise ValueError('Backup checksum mismatch')
    result = reverted_state(json.loads(raw), original, plan)
    if running_desktops() or state_path.read_bytes() != raw:
        raise RuntimeError('Client reopened or state changed; no rollback')
    (folder / 'before-rollback.json').write_bytes(raw)
    save(state_path, result)
    if load(state_path) != result:
        raise RuntimeError('Rollback readback mismatch')
    save(folder / 'rollback-receipt.json', {'state': 'rolled_back', 'sha256': sha(state_path.read_bytes())})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['wait', 'rollback', 'status'])
    parser.add_argument('--plan', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'wait':
        worker(args.plan)
    elif args.action == 'rollback':
        rollback(args.plan)
    else:
        print(json.dumps(load(args.plan.parent / 'worker-status.json'), ensure_ascii=False))


if __name__ == '__main__':
    main()
