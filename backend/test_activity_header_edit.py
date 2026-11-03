"""Teacher header edits affect actual activity values and downstream timing."""
import copy
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from backend.app.activity_formative_api import _validate, _markdown, goal_with_activity_timing


class ActivityHeaderEditTest(TestCase):
    def setUp(self):
        folder = Path(__file__).parent / 'skills/precision-intervention-activity-formative-regulation/tests'
        self.result = json.loads((folder / 'sample-output.json').read_text(encoding='utf-8'))
        self.goal = json.loads((folder / 'sample-input.json').read_text(encoding='utf-8'))['goal_path_design']
        self.goal['intervention_path'] = self.goal['intervention_path']['primary']
        for stage, summary in zip(self.goal['intervention_path']['stages'], self.result['activity_sequence_summary']['stages']):
            stage['stage_name'] = summary['stage_name']

    def edit(self):
        self.result['activities'][0].update(activity_name='案例导入', duration_minutes=8, organization_forms=['个体干预'])
        self.result['activity_sequence_summary']['stages'][0]['duration_minutes'] = 8
        self.result['activity_sequence_summary']['total_duration_minutes'] += 3

    def test_teacher_edits_validate_and_export_actual_values(self):
        self.edit()
        _validate(self.result, self.goal, teacher_edited=True)
        self.assertIn('案例导入（8分钟；个体干预）', _markdown(self.result))
        with self.assertRaises(ValueError):
            _validate(self.result, self.goal)

    def test_downstream_timing_uses_edits_without_mutating_saved_path(self):
        before = copy.deepcopy(self.goal)
        self.edit()
        view = goal_with_activity_timing(self.goal, self.result)
        self.assertEqual(view['intervention_path']['total_duration_minutes'], 48)
        self.assertEqual(view['intervention_path']['stages'][0]['duration_minutes'], 8)
        self.assertEqual(before, self.goal)

    def test_inconsistent_summary_cannot_be_saved(self):
        self.edit()
        self.result['activity_sequence_summary']['total_duration_minutes'] = 45
        with self.assertRaisesRegex(ValueError, '总时长'):
            _validate(self.result, self.goal, teacher_edited=True)


class ActivityHeaderSaveTest(TestCase):
    def setUp(self):
        from backend.test_task_materials import TaskMaterialsTest
        from backend.app import activity_formative_api as api, database as db
        TaskMaterialsTest.setUp(self)
        ActivityHeaderEditTest.setUp(self)
        now = db.utc_now()
        with db.database() as conn:
            conn.execute("""INSERT INTO activity_formative_designs
                (teaching_id,classroom_id,skill_key,skill_version,provider,status,goal_path_hash,
                 generated_json,report_text,generated_at,updated_at)
                VALUES ('pt','class','precision_intervention_activity_formative_regulation','1.2.1',
                        'openrouter','pending_teacher_confirmation','fixture',?,'',?,?)""",
                (json.dumps(self.result), now, now))
    def test_save_and_reload_preserve_header_and_export(self):
        from backend.app import activity_formative_api as api
        ActivityHeaderEditTest.edit(self)
        with patch.object(api, '_source', return_value=(self.goal, {}, {}, {}, 'fixture')):
            response = api.save_activity_formative('pt', 'class', api.ActivitySaveRequest(result=self.result))
            reloaded = api._serialized('pt', 'class')
        activity = reloaded['result']['activities'][0]
        self.assertEqual(activity['activity_name'], '案例导入')
        self.assertEqual(activity['duration_minutes'], 8)
        self.assertEqual(activity['organization_forms'], ['个体干预'])
        self.assertEqual(response['design']['result'], reloaded['result'])
        self.assertIn('案例导入（8分钟；个体干预）', reloaded['report_text'])

    def test_empty_text_can_be_saved_as_draft_and_remains_empty_after_reload(self):
        from fastapi import HTTPException
        from backend.app import activity_formative_api as api
        activity_id = self.result['activities'][0]['activity_id']
        overrides = {activity_id: {'objective_full': ''}}
        with patch.object(api, '_source', return_value=(self.goal, {}, {}, {}, 'fixture')):
            response = api.save_activity_formative('pt', 'class', api.ActivitySaveRequest(result=self.result, presentation_overrides=overrides))
            self.assertEqual(response['design']['presentation_overrides'][activity_id]['objective_full'], '')
            with self.assertRaises(HTTPException) as failure:
                api.save_activity_formative('pt', 'class', api.ActivitySaveRequest(result=self.result, presentation_overrides=overrides, confirmed=True))
            self.assertIn('可先保存草稿', failure.exception.detail)
            self.assertEqual(api._serialized('pt', 'class')['status'], 'pending_teacher_confirmation')
