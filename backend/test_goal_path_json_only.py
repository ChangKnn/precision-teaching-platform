"""One goal/path JSON result feeds both initial and edited teacher reports."""
import copy
import json
from dataclasses import replace
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from fastapi import HTTPException
from jsonschema import ValidationError
from backend import test_task_materials as base
from backend.app import database as db, goal_path_api as api
from backend.app.services import ai
from backend.app.services.skills import load_skill

ROOT=Path(__file__).parent/'skills/precision-intervention-goal-path-design'

class GoalPathJsonOnlyTest(TestCase):
    def setUp(self):
        self.skill=load_skill('precision_intervention_goal_path_design')
        self.result=json.loads((ROOT/'tests/sample-output.json').read_text(encoding='utf-8'))

    def test_actual_request_uses_original_schema_and_unified_rules(self):
        response={'choices':[{'message':{'content':json.dumps(self.result)}}]}
        with patch.object(ai,'settings',replace(ai.settings,ai_api_key='test-only',ai_model='synthetic')), patch.object(ai,'_post_json',return_value=response) as model:
            result,text=ai.run_goal_path_design(self.skill,{},'openrouter')
        self.assertEqual(result,self.result);self.assertIsNone(text)
        body=model.call_args.args[1]
        schema=body['response_format']['json_schema']['schema']
        original=json.loads((ROOT/'assets/goal-path-output.schema.json').read_text(encoding='utf-8'))
        self.assertEqual(schema,ai._rubric_transport_schema(original))
        self.assertIn(json.dumps(original,ensure_ascii=False),body['messages'][0]['content'])
        self.assertNotIn('teacher_report_markdown',schema['properties'])
        instructions=body['messages'][0]['content']
        self.assertNotIn('中小学45分钟单课时默认只安排',instructions)
        self.assertNotIn('高等教育根据教师填写的实际总时长与课程活动需要安排，不套用',instructions)
        self.assertNotIn('先输出教师可读Markdown，再附加',instructions)
        self.assertIn('单课时一般安排一个小组活动时段，最多两个',instructions)
        self.assertIn('不要求另写',instructions)

    def test_missing_json_fields_are_rejected(self):
        result=copy.deepcopy(self.result);del result['common_core_goal']
        with patch.object(ai,'_run_structured_skill',return_value=result),self.assertRaises(ValidationError):
            ai.run_goal_path_design(self.skill,{},'openrouter')

class GoalPathLocalRenderingTest(TestCase):
    setUp=base.TaskMaterialsTest.setUp

    def prepare(self):
        source=json.loads((ROOT/'tests/sample-input.json').read_text(encoding='utf-8'))
        result=json.loads((ROOT/'tests/sample-output.json').read_text(encoding='utf-8'))
        cls=source['diagnosis_bundle']['class_diagnosis'];now=db.utc_now()
        with db.database() as conn:
            conn.execute("INSERT INTO class_diagnosis_reports (teaching_id,classroom_id,skill_key,skill_version,provider,status,source_hash,generated_json,report_text,updated_at) VALUES ('pt','class','solo_class_diagnosis_intervention','1','openrouter','ready','fixture',?,'synthetic',?)",(json.dumps(cls),now))
        report=db.fetch_one("SELECT * FROM class_diagnosis_reports WHERE teaching_id='pt'")
        request=api.GoalPathGenerateRequest(teacher_instructional_context=source['teacher_instructional_context'])
        context=request.teacher_instructional_context
        analysis={'content_confirmed':True,'focus_confirmed':True,'content_analysis':context.teaching_content_and_curriculum_analysis,'focus_analysis':context.teaching_focus_and_difficulty_analysis}
        return source,result,cls,report,request,analysis

    def test_generation_save_and_failure_share_local_report_and_preserve_data(self):
        source,result,cls,report,request,analysis=self.prepare()
        result["common_core_goal"]["goal_statement"] += "（R，关联结构）（R，关联结构）"
        with patch.object(api,'_analysis_draft',return_value=analysis),patch.object(api,'_build_input',return_value=(source,cls,report)),patch.object(api,'run_goal_path_design',return_value=(result,None)):
            response=api.generate_goal_path('pt','class',request)
        generated=db.fetch_one("SELECT * FROM goal_path_designs WHERE teaching_id='pt'")
        expected=api._markdown(json.loads(generated['generated_json']))
        self.assertEqual(generated['report_text'],expected)
        core=expected.split('## 2.')[0]
        self.assertNotIn('目标认知结构及理由',core);self.assertNotIn('达成表现',core)
        self.assertIn('（R，关联结构）',core)
        with patch.object(api,'_source_report',return_value=report),patch.object(api,'_analysis_draft',return_value=analysis):
            api.save_goal_path('pt','class',api.GoalPathSaveRequest(result=json.loads(generated['generated_json'])))
        saved=db.fetch_one("SELECT * FROM goal_path_designs WHERE teaching_id='pt'")
        self.assertEqual(saved['report_text'],generated['report_text'])
        revised=request.model_copy(update={'regeneration_request':'保持核心目标，精简说明'})
        with patch.object(api,'_analysis_draft',return_value=analysis),patch.object(api,'_build_input',return_value=(source,cls,report)),patch.object(api,'run_goal_path_design',side_effect=RuntimeError('synthetic interrupted generation')),self.assertRaises(HTTPException):
            api.generate_goal_path('pt','class',revised)
        self.assertEqual(db.fetch_one("SELECT * FROM goal_path_designs WHERE teaching_id='pt'"),saved)
