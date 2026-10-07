"""Portable storage path behavior, without touching the working database."""

import tempfile
import unittest
from pathlib import Path

from backend.app.config import PROJECT_ROOT, project_path


class PlatformPathTest(unittest.TestCase):
    def test_relative_path_is_based_on_project_root(self):
        self.assertEqual(project_path("./data/uploads"), PROJECT_ROOT / "data/uploads")

    def test_absolute_path_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "中文目录" / "example.db"
            self.assertEqual(project_path(str(target)), target)
