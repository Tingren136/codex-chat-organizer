"""Install code only; keep private configuration, caches and reports in place."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import quote
from urllib.request import urlopen
import zipfile


def download_source(version, target):
    # Resolve the requested ref, never silently replace a failed tag with main.
    url = 'https://api.github.com/repos/Tingren136/codex-chat-organizer/commits/' + quote(version, safe='')
    with urlopen(url, timeout=60) as response:
        revision = json.load(response)['sha']
    archive = target / 'source.zip'
    with urlopen('https://codeload.github.com/Tingren136/codex-chat-organizer/zip/' + revision, timeout=90) as r:
        archive.write_bytes(r.read())
    source = target / 'source'
    with zipfile.ZipFile(archive) as z:
        for item in z.infolist():
            parts = item.filename.split('/')[1:]
            if parts[:2] != ['skills', 'chat-organizer'] or item.is_dir():
                continue
            dest = source.joinpath(*parts).resolve()
            if not dest.is_relative_to(source.resolve()):
                raise ValueError('Unsafe archive path')
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(z.read(item))
    return source, revision


def install(source, home, revision='local'):
    source = source.resolve() / 'skills' / 'chat-organizer'
    home = home.resolve()
    destination = home / 'skills' / 'chat-organizer'
    if not (source / 'SKILL.md').is_file() or not (source / 'scripts/organize.py').is_file():
        raise ValueError('Incomplete source package')
    if source == destination or source.is_relative_to(destination):
        raise ValueError('Source must be separate from installed skill')
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Stage beside destination, then rename. Backups are outside skill discovery.
    with tempfile.TemporaryDirectory(prefix='.organizer-stage-', dir=home) as temporary:
        staged = Path(temporary) / 'chat-organizer'
        shutil.copytree(source, staged, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.git'))
        env = dict(os.environ, CODEX_HOME=str(home))
        checked = subprocess.run([sys.executable, '-X', 'utf8', str(staged / 'scripts/organize.py'),
                                  'doctor', '--installation'], env=env, capture_output=True, text=True,
                                  encoding='utf-8', timeout=60)
        if checked.returncode:
            raise RuntimeError('Installation self-check failed; existing version unchanged. ' + checked.stdout)
        status = json.loads(checked.stdout)
        backup = None
        if destination.exists():
            backup = home / 'skill-backups' / ('chat-organizer-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
            backup.parent.mkdir(parents=True, exist_ok=True)
            destination.rename(backup)
        try:
            staged.rename(destination)
        except Exception:
            if backup is not None:
                backup.rename(destination)
            raise
    return {'installed': True, 'version': status['version'], 'revision': revision,
            'skill_dir': str(destination), 'backup': str(backup) if backup else None,
            'data_dir': status['data_dir'], 'output_dir': status['output_dir'],
            'execution_environment_ready': status['execution_environment_ready'],
            'checks_missing': status['checks_missing'], 'authentication_checked': False,
            'next': 'In a Codex desktop chat, run doctor before organizing. No chats were read or changed.'}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--codex-home', type=Path, default=Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')))
    p.add_argument('--version', default='v0.4.1')
    p.add_argument('--source', type=Path, help='Install from an already downloaded repository')
    args = p.parse_args()
    if os.name != 'nt' or sys.version_info[:2] != (3, 14):
        p.error('Requires Windows and Python 3.14: https://www.python.org/downloads/windows/')
    if args.source:
        result = install(args.source, args.codex_home)
    else:
        with tempfile.TemporaryDirectory(prefix='chat-organizer-download-') as temporary:
            source, revision = download_source(args.version, Path(temporary))
            result = install(source, args.codex_home, revision)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
