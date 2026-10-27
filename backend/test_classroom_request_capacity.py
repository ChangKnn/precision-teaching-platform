"""Long model waits must not consume all capacity for a class of forty."""
import asyncio
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from anyio import to_thread
from backend.app import database as db, main


class ClassroomRequestCapacityTest(unittest.TestCase):
    def test_startup_reserves_capacity_for_forty_students_and_other_requests(self):
        with TemporaryDirectory() as folder:
            isolated=replace(main.settings,database_path=Path(folder)/'test.db',upload_path=Path(folder)/'uploads',
                             seed_demo_data=False,request_thread_limit=96)
            async def check():
                async with main.lifespan(main.app):
                    self.assertGreaterEqual(to_thread.current_default_thread_limiter().total_tokens,80)
            with patch.object(db,'settings',isolated),patch.object(main,'settings',isolated):
                asyncio.run(check())
