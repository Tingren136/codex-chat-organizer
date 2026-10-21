"""Portable local settings. Never print inherited desktop connection values."""
import json
import os
from pathlib import Path
import subprocess

VERSION = '0.3.0'


def absolute(value):
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError('Use an absolute local path: ' + str(path))
    return path


def codex_home():
    return absolute(os.environ.get('CODEX_HOME') or Path.home() / '.codex')


def config():
    path = absolute(os.environ.get('CHAT_ORGANIZER_SETTINGS') or codex_home() / 'chat-organizer.local.json')
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or set(value) - {'data_dir', 'output_dir', 'resources_dir'}:
        raise ValueError('Unsupported local setting; use data_dir/output_dir/resources_dir')
    return value


def data_root():
    return absolute(os.environ.get('CHAT_ORGANIZER_DATA_DIR') or config().get('data_dir')
                    or codex_home() / 'chat-organizer-data')


def output_dir():
    return absolute(os.environ.get('CHAT_ORGANIZER_OUTPUT_DIR') or config().get('output_dir')
                    or data_root() / 'reports')


def desktop_server():
    relative = Path('plugins/openai-bundled/plugins/codex-app-tools/server.mjs')
    explicit = os.environ.get('CHAT_ORGANIZER_RESOURCES') or config().get('resources_dir')
    if explicit:
        server = absolute(explicit) / relative
        if not server.is_file():
            raise RuntimeError('Official desktop server missing from explicit resources_dir')
        return server
    if os.name != 'nt':
        raise RuntimeError('This desktop adapter currently supports Windows only')
    # Inspect the installed app registration, not logs, browser sessions or pipes.
    command = "@(Get-AppxPackage -Name OpenAI.Codex | Select-Object -ExpandProperty InstallLocation) | ConvertTo-Json -Compress"
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                            capture_output=True, text=True, encoding='utf-8', timeout=15,
                            creationflags=subprocess.CREATE_NO_WINDOW, check=True)
    roots = json.loads(result.stdout.strip() or '[]')
    if isinstance(roots, str):
        roots = [roots]
    found = [Path(root) / 'app/resources' / relative for root in roots
             if (Path(root) / 'app/resources' / relative).is_file()]
    if len(found) != 1:
        raise RuntimeError('Cannot uniquely locate official desktop resources; set resources_dir')
    return found[0]
