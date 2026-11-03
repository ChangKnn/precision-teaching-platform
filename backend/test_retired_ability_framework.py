"""Retired theme frameworks stay recoverable and cannot affect task generation."""
import json
from pathlib import Path
from unittest import TestCase
from backend import test_task_version_runtime as runtime_tests
from backend.app import database as db, main, diagnostic_task_design_api as design
from backend.app.services.skills import list_skills

class RetiredAbilityFrameworkTest(TestCase):
    setUp = runtime_tests.TaskVersionRuntimeTest.setUp
    confirm_rubric = runtime_tests.TaskVersionRuntimeTest.confirm_rubric
    publish = runtime_tests.TaskVersionRuntimeTest.publish
    save = runtime_tests.TaskVersionRuntimeTest.save

    def test_historical_framework_is_preserved_but_not_used(self):
        with db.database() as connection:
            connection.execute("INSERT INTO ability_solo_frameworks (teaching_id,ability_name,levels_json,revision,updated_at) VALUES (?,?,?,?,?)", ('pt','历史能力',json.dumps({'R':'历史框架'}),1,db.utc_now()))
        before = db.fetch_one("SELECT * FROM ability_solo_frameworks WHERE teaching_id='pt'")
        self.assertNotIn('target_ability_framework',main.build_rubric_input('pt'))
        self.assertNotIn('target_ability_framework',design._source('pt')[0])
        self.assertNotIn('ability_framework',main.workspace_for('pt'))
        self.assertEqual(before,db.fetch_one("SELECT * FROM ability_solo_frameworks WHERE teaching_id='pt'"))

    def test_retired_api_and_skill_are_not_exposed(self):
        self.assertFalse(any('ability-framework' in getattr(route,'path','') for route in main.app.routes))
        self.assertNotIn('solo_ability_framework',{skill['key'] for skill in list_skills()})

    def test_standard_input_still_requires_design_intent(self):
        root = Path(__file__).parent/'skills'
        schema=json.loads((root/'solo-task-rubric-builder/assets/task-rubric-input.schema.json').read_text(encoding='utf-8'))
        self.assertIn('diagnostic_task_design_intent',schema['properties'])
        self.assertNotIn('target_ability_framework',schema['properties'])
