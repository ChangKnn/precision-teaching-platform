"""Teacher ideas -> generation Skill -> candidate intent, without model calls."""
import asyncio
import json
from unittest import TestCase
from unittest.mock import patch
from jsonschema import ValidationError as SchemaValidationError

from fastapi import HTTPException, Request
from pydantic import ValidationError

from backend import test_task_materials as legacy
from backend.app import database as db, diagnostic_task_design_api as api, main
from backend.app.services.ai import run_diagnostic_task_design
from backend.app.services.skills import load_skill
from backend.app.services.student_dialogue_skill import freeze_dialogue_config


class GenerationIdeasTest(TestCase):
    setUp = legacy.TaskMaterialsTest.setUp

    def sample(self):
        return json.loads((load_skill('precision_diagnostic_task_design').path.parent/'tests/sample-output.json').read_text(encoding='utf-8'))

    def save(self,text):
        return api.save_preferences('pt',api.TaskDesignPreferences(teacher_ideas=text))

    def generate(self):
        with patch.object(api,'run_diagnostic_task_design',side_effect=lambda skill,payload,provider:run_diagnostic_task_design(skill,payload,'deepseek')):
            with patch('backend.app.services.ai._run_structured_skill',return_value=self.sample()) as model:
                result=api.generate_recommendations('pt')
        return result,model.call_args.args[1]

    def test_blank_ideas_keeps_context_only_generation(self):
        self.save('  ')
        result,payload=self.generate()
        self.assertEqual(set(payload),{'precision_teaching_context'})
        self.assertEqual(result['teacher_ideas'],'')
        self.assertEqual(len(result['result']['diagnostic_task_design']['candidates']),3)

    def test_teacher_ideas_reach_actual_skill_contract(self):
        self.save('请观察方法比较，使用生活情境并提交反思。')
        result,payload=self.generate()
        self.assertEqual(payload['teacher_ideas'],'请观察方法比较，使用生活情境并提交反思。')
        self.assertEqual(api.get_recommendations('pt')['status'],'ready')
        self.assertEqual(result['result']['schema_version'],'2.1')
        self.assertTrue(all(c['design_intent'].strip() for c in result['result']['diagnostic_task_design']['candidates']))
        stored=db.fetch_one("SELECT input_json FROM ai_jobs ORDER BY created_at DESC LIMIT 1")
        self.assertEqual(json.loads(stored['input_json'])['teacher_ideas'],payload['teacher_ideas'])

    def test_saving_ideas_never_changes_adopted_task_or_intent(self):
        before=db.fetch_one("SELECT * FROM diagnosis_tasks WHERE teaching_id='pt'")
        self.save('改变候选方案的生成方向')
        self.assertEqual(db.fetch_one("SELECT * FROM diagnosis_tasks WHERE teaching_id='pt'"),before)
        self.assertEqual(main.workspace_for('pt')['task_design_preferences']['teacher_ideas'],'改变候选方案的生成方向')

    def test_changed_ideas_invalidate_recommendations_not_the_current_task(self):
        self.save('方向一');self.generate()
        old_hash=api.get_recommendations('pt')['input_hash']
        self.save('方向二')
        response=api.get_recommendations('pt')
        self.assertEqual(response['status'],'stale')
        self.assertIsNone(response['result'])
        self.assertNotEqual(response['input_hash'],old_hash)
        self.save('')
        self.assertNotIn('teacher_ideas',api._source('pt')[0])

    def test_concurrent_edit_does_not_store_stale_generation(self):
        self.save('原要求');self.generate()
        original=db.fetch_one("SELECT * FROM diagnostic_task_recommendations WHERE teaching_id='pt'")
        def edited(skill,payload,provider):
            self.save('生成中改变要求')
            return self.sample()
        with patch.object(api,'run_diagnostic_task_design',side_effect=edited),self.assertRaises(HTTPException) as error:
            api.generate_recommendations('pt')
        self.assertEqual(error.exception.status_code,409)
        self.assertEqual(db.fetch_one("SELECT * FROM diagnostic_task_recommendations WHERE teaching_id='pt'"),original)
        self.assertEqual(db.fetch_one("SELECT status FROM ai_jobs ORDER BY created_at DESC LIMIT 1")['status'],'failed')

    def test_missing_candidate_intent_is_not_silently_accepted(self):
        sample=self.sample();del sample['diagnostic_task_design']['candidates'][0]['design_intent']
        with patch('backend.app.services.ai._run_structured_skill',return_value=sample),self.assertRaises(SchemaValidationError):
            run_diagnostic_task_design(load_skill('precision_diagnostic_task_design'),api._source('pt')[0],'deepseek')

    def test_mock_still_does_not_call_real_model(self):
        self.save('私有想法')
        with patch('backend.app.services.ai._run_structured_skill') as model,self.assertRaises(RuntimeError):
            run_diagnostic_task_design(load_skill('precision_diagnostic_task_design'),api._source('pt')[0],'mock')
        model.assert_not_called()

    def test_size_limit_is_enforced(self):
        with self.assertRaises(ValidationError):api.TaskDesignPreferences(teacher_ideas='字'*4001)

    def test_student_contract_never_contains_generation_ideas(self):
        self.save('教师私有生成要求标记')
        with db.database() as c:c.execute("UPDATE diagnosis_tasks SET status='published' WHERE teaching_id='pt'")
        frozen=freeze_dialogue_config('pt')
        self.assertNotIn('教师私有生成要求标记',json.dumps(frozen,ensure_ascii=False))

    def test_other_teacher_cannot_edit_preferences(self):
        request=Request({'type':'http','method':'PUT','path':'/api/precision-teachings/pt/diagnostic-task-preferences','headers':[]})
        async def next_request(_):raise AssertionError('must not reach foreign project')
        other_teacher = {'account_id': 'other-teacher', 'role': 'teacher',
                         'workspace_id': 'other', 'must_change_password': False}
        with patch.object(main,'current_account',return_value=other_teacher):response=asyncio.run(main.protect_teacher_api(request,next_request))
        self.assertEqual(response.status_code,404)
