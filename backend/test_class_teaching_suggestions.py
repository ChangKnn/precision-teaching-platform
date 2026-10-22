"""Source-bound, short teaching ideas must not rewrite diagnosis or student data."""
import copy
from dataclasses import replace
import json
from unittest import TestCase
from unittest.mock import patch

from fastapi import HTTPException, Request
from jsonschema import ValidationError

from backend import test_task_materials as base
from backend.app import database as db
from backend.app.services import class_teaching_suggestions as api
from backend.app.services.ai import _skill_runtime_instructions
from backend.app.services.skills import load_skill


class ClassTeachingSuggestionsTest(TestCase):
    def setUp(self):
        base.TaskMaterialsTest.setUp(self)
        target = patch.object(api,'settings',replace(self.settings,ai_provider='openrouter'))
        target.start();self.addCleanup(target.stop)
        self.payload=json.loads((load_skill('solo_class_diagnosis_intervention').path.parent/'tests/sample-input.json').read_text(encoding='utf-8'))
        source=patch('backend.app.services.class_report.class_report_input',return_value=(self.payload,'h',{}))
        source.start();self.addCleanup(source.stop)
        self.source=source
        now=db.utc_now()
        with db.database() as connection:
            connection.execute("""INSERT INTO class_diagnosis_reports
                (teaching_id,classroom_id,skill_key,skill_version,provider,status,source_hash,generated_json,report_text,generated_at,updated_at)
                VALUES ('pt','class','solo_class_diagnosis_intervention','1.1.0','openrouter','ready','h','{}','既有诊断',?,?)""",(now,now))
        self.request=Request({'type':'http','method':'POST','path':'/api/precision-teachings/pt/classrooms/class/teaching-suggestions/generate','headers':[]})
        self.request.state.teacher_workspace_id='ws'

    def sample(self):
        payload=api.build_input('pt','class')
        return {'items':[{'level':need['level'],'target_level':need['target_level'],
             'development_goal':'依据条件说明方法的选择理由。','content':'同一角度问题的两种表示。',
             'activity':'比较条件并写出选择依据。','scaffold':'提供空白比较表，逐渐撤除提示。'} for need in payload['level_needs']]}

    def generate(self, sample=None):
        with patch.object(api,'_run_structured_skill',return_value=sample or self.sample()) as model:
            response=api.generate_teaching_suggestions('pt','class',self.request)
        return response,model.call_args.args[1]

    def test_input_carries_context_actual_obstacles_and_goals(self):
        payload=api.build_input('pt','class')
        self.assertEqual(payload['precision_teaching_context'],self.payload['precision_teaching_context'])
        self.assertEqual(sum(item['count'] for item in payload['level_needs']),len(self.payload['student_diagnosis_results']))
        self.assertTrue(all(item['observed_obstacles'] and item['progression_goals'] for item in payload['level_needs']))

    def test_generation_preserves_class_and_individual_data(self):
        before=db.fetch_one("SELECT * FROM class_diagnosis_reports WHERE teaching_id='pt'")
        response,payload=self.generate()
        self.assertEqual(db.fetch_one("SELECT * FROM class_diagnosis_reports WHERE teaching_id='pt'"),before)
        self.assertEqual(response['teaching_suggestions']['provider'],'openrouter')
        self.assertEqual([i['level'] for i in response['teaching_suggestions']['items']],[i['level'] for i in payload['level_needs']])
        self.assertTrue(all(i['count']>0 for i in response['teaching_suggestions']['items']))
        self.assertEqual(db.fetch_one('SELECT workspace_id FROM ai_jobs')['workspace_id'],'ws')

    def test_old_source_is_never_reused(self):
        self.generate()
        self.assertIsNone(api.latest_suggestions('pt','class','changed'))
        self.assertIsNone(api.latest_suggestions('pt','other-class','h'))
        self.assertIsNone(api.latest_suggestions('other-teaching','class','h'))

    def test_duplicate_or_missing_level_rejected(self):
        payload=api.build_input('pt','class');sample=self.sample()
        sample['items'].append(copy.deepcopy(sample['items'][0]))
        with self.assertRaises(ValueError):api.validate_suggestions(sample,payload)
        sample=self.sample();sample['items'].pop()
        with self.assertRaises(ValueError):api.validate_suggestions(sample,payload)

    def test_wrong_target_rejected(self):
        sample=self.sample();sample['items'][0]['target_level']='higher_EA'
        with self.assertRaises(ValueError):api.validate_suggestions(sample,api.build_input('pt','class'))

    def test_missing_field_and_long_text_rejected(self):
        sample=self.sample();del sample['items'][0]['scaffold']
        with self.assertRaises(ValidationError):api.validate_suggestions(sample,api.build_input('pt','class'))
        sample=self.sample();sample['items'][0]['activity']='字'*61
        with self.assertRaises(ValidationError):api.validate_suggestions(sample,api.build_input('pt','class'))
        sample=self.sample()
        for key in api.FIELDS:sample['items'][0][key]='字'*60
        with self.assertRaises(ValueError):api.validate_suggestions(sample,api.build_input('pt','class'))

    def test_failed_regeneration_keeps_saved_suggestions(self):
        self.generate();before=api.latest_suggestions('pt','class','h')
        with patch.object(api,'_run_structured_skill',side_effect=RuntimeError('测试模型失败')):
            with self.assertRaises(HTTPException) as error:api.generate_teaching_suggestions('pt','class',self.request)
        self.assertEqual(error.exception.status_code,502)
        self.assertEqual(api.latest_suggestions('pt','class','h'),before)

    def test_changed_source_during_generation_not_saved(self):
        sample=self.sample()
        def change(*args):
            with db.database() as connection:connection.execute("UPDATE class_diagnosis_reports SET source_hash='new' WHERE teaching_id='pt'")
            return sample
        with patch.object(api,'_run_structured_skill',side_effect=change):
            with self.assertRaises(HTTPException) as error:api.generate_teaching_suggestions('pt','class',self.request)
        self.assertEqual(error.exception.status_code,409)
        self.assertIsNone(api.latest_suggestions('pt','class','h'))

    def test_unready_class_is_blocked(self):
        with db.database() as connection:connection.execute("UPDATE class_diagnosis_reports SET status='running'")
        with self.assertRaises(HTTPException) as error:api.build_input('pt','class')
        self.assertEqual(error.exception.status_code,409)

    def test_mock_provider_is_not_used_for_real_ideas(self):
        with patch.object(api,'settings',replace(self.settings,ai_provider='mock')):
            with self.assertRaises(HTTPException) as error:api.generate_teaching_suggestions('pt','class',self.request)
        self.assertEqual(error.exception.status_code,409)

    def test_thesis_rules_reach_actual_runtime_prompt(self):
        prompt=_skill_runtime_instructions(load_skill(api.KEY))
        for text in ['thesis-progression-rules.md','去情境化','关系由学生自己建构','class_teaching_suggestions JSON Schema']:
            self.assertIn(text,prompt)
