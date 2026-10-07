"""Launcher safety checks: no working data or credentials are touched."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import start


class LauncherTest(unittest.TestCase):
    def test_platform_specific_virtualenv_executable(self):
        with patch.object(start.sys, "platform", "win32"):
            self.assertEqual(start.environment_python(Path("project")), Path("project/.venv/Scripts/python.exe"))
        with patch.object(start.sys, "platform", "darwin"):
            self.assertEqual(start.environment_python(Path("project")), Path("project/.venv/bin/python"))

    @patch("start.subprocess.run")
    def test_preserves_existing_config_and_only_installs_on_change(self, run):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            python = start.environment_python(root)
            python.parent.mkdir(parents=True)
            python.touch()
            (root / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (root / ".env").write_text("existing-config", encoding="utf-8")
            (root / ".env.example").write_text("sample-config", encoding="utf-8")
            start.prepare_environment(root)
            start.prepare_environment(root)
            self.assertEqual(run.call_count, 1)
            self.assertEqual((root / ".env").read_text(encoding="utf-8"), "existing-config")
            (root / "requirements.txt").write_text("fastapi\nuvicorn\n", encoding="utf-8")
            start.prepare_environment(root)
            self.assertEqual(run.call_count, 2)

    @patch("start.prepare_environment")
    @patch("start.port_in_use", return_value=True)
    @patch.object(start.sys, "argv", ["start.py"])
    def test_occupied_port_does_not_launch_or_modify_environment(self, _port, prepare):
        self.assertEqual(start.main(), 1)
        prepare.assert_not_called()
