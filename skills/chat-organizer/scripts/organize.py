"""Self-contained Skill entrypoint. Persistent data lives outside this package."""
import json
import os
from pathlib import Path
import runpy
import shutil
import sqlite3
import sys

RUNTIME = Path(__file__).resolve().parent / 'runtime'
sys.path.insert(0, str(RUNTIME))
from settings import VERSION, data_root, codex_home, desktop_server, output_dir


def doctor():
    root = data_root()
    result = {'version': VERSION, 'runtime': str(RUNTIME), 'data_dir': str(root),
              'output_dir': str(output_dir()), 'platform_supported': os.name == 'nt',
              'codex_cli_found': bool(shutil.which('codex')),
              'desktop_environment_supplied': all(bool(os.environ.get(k)) for k in
                    ('CODEX_THREAD_ID', 'CODEX_MCP_NODE_PATH', 'CODEX_APP_TOOLS_PIPE_PATH')),
              'model_calls': 0, 'chat_mutations': 0}
    try:
        result['official_server_found'] = desktop_server().is_file()
    except Exception as exc:
        result['official_server_found'] = False
        result['installation_issue'] = str(exc)
    db = root / 'work/organizer-cache.sqlite'
    result['existing_state_found'] = db.is_file()
    if db.is_file():
        connection = sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)
        try:
            for table, field in [('locks', 'locked_chats'), ('model_jobs', 'historical_model_batches')]:
                found = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
                result[field] = connection.execute('SELECT count(*) FROM ' + table).fetchone()[0] if found else 0
        finally:
            connection.close()
    print(json.dumps(result, ensure_ascii=False))
    return result


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    args = sys.argv[1:]
    if args == ['doctor']:
        doctor(); return
    if not args or args == ['--help']:
        print('chat-organizer ' + VERSION + '\n'
              'doctor | scan --count 20 | report --session-id ID | apply-titles --session-id ID --approval FILE | apply --session-id ID --approval FILE\n'
              'prepare-projects --session-id ID --approval FILE --roots FILE [--repair-confirmation TEXT]\n'
              'arm-repair --plan PATCH_PLAN | resume --plan PLAN | repair-status --plan PLAN\n'
              'Offline rollback: runtime/offline_registration.py rollback --plan PATCH_PLAN')
        return
    if os.name != 'nt':
        raise SystemExit('This release supports Windows Codex Desktop only')
    root = data_root()
    (root / 'work').mkdir(parents=True, exist_ok=True)
    if args[0] in ('prepare-projects', 'arm-repair'):
        runpy.run_path(str(RUNTIME / 'project_admin.py'), run_name='__main__')
    elif args[0] == 'resume':
        sys.argv = [sys.argv[0], *args[1:]]
        runpy.run_path(str(RUNTIME / 'resume_groups.py'), run_name='__main__')
    elif args[0] == 'repair-status':
        sys.argv = [sys.argv[0], 'status', *args[1:]]
        runpy.run_path(str(RUNTIME / 'offline_registration.py'), run_name='__main__')
    else:
        runpy.run_path(str(RUNTIME / 'review_workflow.py'), run_name='__main__')


if __name__ == '__main__':
    main()
