import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


entry = module('organizer_entry', ROOT / 'skills/chat-organizer/scripts/organize.py')
installer = module('organizer_installer', ROOT / 'tools/install.py')


class InstallationTests(unittest.TestCase):
    def test_missing_cli_and_connection_not_execution_success(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'CODEX_HOME': tmp}, clear=True), \
             patch.object(entry.shutil, 'which', return_value=None), \
             patch.object(entry, 'desktop_server', return_value=Path(tmp) / 'absent'), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(entry.doctor()['ok'])
            result = entry.doctor(installation=True)
            self.assertTrue(result['installation_ok'])
            self.assertFalse(result['execution_environment_ready'])
            self.assertFalse(result['authentication_checked'])

    def test_doctor_exit_is_nonzero_without_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {k: v for k, v in os.environ.items() if not k.startswith(('CODEX_', 'CHAT_ORGANIZER_'))}
            env['CODEX_HOME'] = tmp
            proc = subprocess.run([sys.executable, str(ROOT / 'skills/chat-organizer/scripts/organize.py'),
                                   'doctor'], env=env, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(proc.returncode, 2)

    def test_upgrade_preserves_config_and_data_no_nested_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / 'home'
            home.mkdir()
            config = home / 'chat-organizer.local.json'
            data = Path(tmp) / 'private-data'
            data.mkdir()
            marker = data / 'processed.txt'
            marker.write_text('keep this', encoding='utf-8')
            config.write_text(json.dumps({'data_dir': str(data)}), encoding='utf-8')
            original = config.read_bytes()
            with patch.dict(os.environ, {}, clear=True):
                first = installer.install(ROOT, home)
                second = installer.install(ROOT, home)
            self.assertTrue(first['installed'])
            self.assertEqual(second['data_dir'], str(data))
            self.assertEqual(config.read_bytes(), original)
            self.assertEqual(marker.read_text(), 'keep this')
            self.assertTrue((Path(second['backup']) / 'SKILL.md').exists())
            self.assertFalse((home / 'skills/chat-organizer/chat-organizer').exists())
            self.assertFalse((home / 'chat-organizer-data').exists())

    def test_failed_preflight_preserves_old_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            destination = home / 'skills/chat-organizer'
            destination.mkdir(parents=True)
            (destination / 'SKILL.md').write_text('previous')
            failed = subprocess.CompletedProcess([], 2, stdout='failed')
            with patch.object(installer.subprocess, 'run', return_value=failed):
                with self.assertRaises(RuntimeError):
                    installer.install(ROOT, home)
            self.assertEqual((destination / 'SKILL.md').read_text(), 'previous')
