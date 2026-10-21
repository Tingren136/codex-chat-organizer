import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import settings


class PortableSettingsTests(unittest.TestCase):
    def test_user_settings_preserve_existing_data_without_copying(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            config = root / 'config.json'
            config.write_text(json.dumps({'data_dir': str(root / 'existing'),
                                           'output_dir': str(root / 'reports')}), encoding='utf-8')
            with patch.dict(os.environ, {'CHAT_ORGANIZER_SETTINGS': str(config)}, clear=True):
                self.assertEqual(settings.data_root(), root / 'existing')
                self.assertEqual(settings.output_dir(), root / 'reports')
                self.assertFalse((root / 'existing').exists())

    def test_environment_overrides_private_settings(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.dict(os.environ, {'CODEX_HOME': d, 'CHAT_ORGANIZER_DATA_DIR': d}, clear=True):
                self.assertEqual(settings.data_root(), Path(d))

    def test_default_keeps_data_outside_installed_skill(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.dict(os.environ, {'CODEX_HOME': d}, clear=True):
                self.assertEqual(settings.data_root(), Path(d) / 'chat-organizer-data')

    def test_relative_storage_is_rejected(self):
        with patch.dict(os.environ, {'CHAT_ORGANIZER_DATA_DIR': 'relative/private'}, clear=True):
            with self.assertRaises(ValueError):
                settings.data_root()

    def test_explicit_official_resources_resolution(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            server = root / 'plugins/openai-bundled/plugins/codex-app-tools/server.mjs'
            server.parent.mkdir(parents=True)
            server.write_text('// fixture', encoding='utf-8')
            with patch.dict(os.environ, {'CHAT_ORGANIZER_RESOURCES': d}, clear=True):
                self.assertEqual(settings.desktop_server(), server)

    def test_no_silent_fallback_when_explicit_resources_invalid(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.dict(os.environ, {'CHAT_ORGANIZER_RESOURCES': d}, clear=True):
                with self.assertRaises(RuntimeError):
                    settings.desktop_server()


if __name__ == '__main__':
    unittest.main()
