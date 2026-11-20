"""Failed or outdated generations cannot overwrite a teacher's saved design."""
import copy
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from fastapi import HTTPException

from backend.test_task_materials import TaskMaterialsTest
from backend.app import activity_formative_api as api, database as db

SAMPLES=Path(__file__).parent/'skills/precision-intervention-activity-formative-regulation/tests'


class ActivityGenerationSafetyTest(TestCase):
    setUp=TaskMaterialsTest.setUp

    def prepare(self):
        source=json.loads((SAMPLES/'sample-input.json').read_text())
        goal=copy.deepcopy(source['goal_path_design'])
        goal['intervention_path']=goal['intervention_path']['primary']
        now=db.utc_now()
        with db.database() as connection:
            connection.execute("""INSERT INTO activity_formative_designs
                (teaching_id,classroom_id,skill_key,skill_version,provider,status,goal_path_hash,
                 generated_json,report_text,generated_at,updated_at)
                VALUES ('pt','class','test','1','test','teacher_confirmed','old','{"saved":true}','教师已有方案',?,?)""",(now,now))
        return source,goal

    def assert_saved(self):
        row=db.fetch_one("SELECT generated_json,report_text,status FROM activity_formative_designs WHERE teaching_id='pt'")
        self.assertEqual(row,{'generated_json':'{"saved":true}','report_text':'教师已有方案','status':'teacher_confirmed'})
        self.assertEqual(db.fetch_one("SELECT status FROM ai_jobs ORDER BY created_at DESC LIMIT 1")['status'],'failed')

    def test_failed_repair_preserves_saved_design(self):
        source,goal=self.prepare()
        with patch.object(api,'_build_input',return_value=(source,goal,'old')), patch.object(api,'_generate_validated_activity',side_effect=ValueError('仍不符合时长')):
            with self.assertRaises(HTTPException) as caught:
                api.generate_activity_formative('pt','class',api.ActivityGenerateRequest())
        self.assertEqual(caught.exception.status_code,502)
        self.assert_saved()

    def test_upstream_change_during_generation_rejects_stale_result(self):
        source,goal=self.prepare()
        result=json.loads((SAMPLES/'sample-output.json').read_text())
        with patch.object(api,'_build_input',return_value=(source,goal,'old')), patch.object(api,'_generate_validated_activity',return_value=result), patch.object(api,'_source',return_value=(goal,{}, {}, {},'new')):
            with self.assertRaises(HTTPException) as caught:
                api.generate_activity_formative('pt','class',api.ActivityGenerateRequest())
        self.assertEqual(caught.exception.status_code,409)
        self.assert_saved()
