"""Optional university trial context must not alter the school-first workflow."""
import copy
import json
from unittest import TestCase
from unittest.mock import patch

import jsonschema
from fastapi import Request
from pydantic import ValidationError

from backend import test_task_materials as base
from backend.app import database as db, main, diagnostic_task_design_api as design
from backend.app.schemas import PrecisionTeachingDraft, PrecisionTeachingUpdate
from backend.app.services.education_context import higher_education_fields
from backend.app.services.skills import load_skill
from backend.app.services.student_dialogue_skill import freeze_dialogue_config, _runtime_instructions
from backend.app.services.ai import _skill_runtime_instructions, run_student_diagnosis_report


class HigherEducationTest(TestCase):
    setUp=base.TaskMaterialsTest.setUp

    def university(self):
        with db.database() as connection:
            connection.execute("""UPDATE precision_teachings SET education_stage='高等教育',grade='大二',
                course_name='教育心理学',major='学前教育',textbook='课程大纲：学习动机单元',
                title='学习动机理论在案例解释中的应用',goal='比较理论并说明案例解释依据',content='动机理论与课堂案例' WHERE id='pt'""")
        return db.fetch_one("SELECT * FROM precision_teachings WHERE id='pt'")

    def request(self):
        request=Request({'type':'http','method':'POST','path':'/api/precision-teachings','headers':[]})
        request.state.teacher_workspace_id='ws'
        return request

    def test_school_defaults_and_optional_university_fields(self):
        payload=PrecisionTeachingDraft()
        self.assertEqual(payload.grade,'高一')
        self.assertEqual(payload.education_stage,'')
        self.assertEqual(higher_education_fields({'grade':'高一','education_stage':'高中'}),{})
        self.assertEqual(higher_education_fields({'grade':'大一'})['education_stage'],'高等教育')
        with self.assertRaises(ValidationError):PrecisionTeachingDraft(education_stage='高等教育',grade='高一')
        with self.assertRaises(ValidationError):PrecisionTeachingDraft(education_stage='高中',grade='大一')

    def test_create_and_edit_university_context(self):
        with db.database() as connection:connection.execute("UPDATE teacher_workspaces SET profile_completed=1 WHERE id='ws'")
        payload=PrecisionTeachingDraft(title='大学课程试用',goal='说明证据',content='学习动机案例',grade='大二',education_stage='高等教育',course_name='教育心理学',major='学前教育')
        result=main.create_precision_teaching_draft(payload,self.request())
        self.assertEqual(result['course_name'],'教育心理学')
        self.assertEqual(result['major'],'学前教育')
        changed=main.update_precision_teaching(result['id'],PrecisionTeachingUpdate(**{**payload.model_dump(),'course_name':'教育心理学专题'}),self.request())
        self.assertEqual(changed['course_name'],'教育心理学专题')

    def test_old_client_update_does_not_erase_new_metadata(self):
        teaching=self.university()
        old={key:teaching[key] for key in ('title','goal','content','rationale','subject','grade','textbook','estimated_periods')}
        result=main.update_precision_teaching('pt',PrecisionTeachingUpdate(**old),self.request())
        self.assertEqual(result['education_stage'],'高等教育')
        self.assertEqual(result['course_name'],'教育心理学')

    def test_draft_to_task_rubric_and_frozen_dialogue_context(self):
        self.university()
        task_context=design._source('pt')[0]['precision_teaching_context']
        rubric_context=main.build_rubric_input('pt')['precision_teaching_context']
        with db.database() as connection:connection.execute("UPDATE diagnosis_tasks SET status='published' WHERE teaching_id='pt'")
        frozen=freeze_dialogue_config('pt')['precision_teaching_context']
        for context in (task_context,rubric_context,frozen):
            self.assertEqual(context['education_stage'],'高等教育')
            self.assertEqual(context['course_name'],'教育心理学')
            self.assertEqual(context['major'],'学前教育')

    def test_all_context_schemas_accept_optional_university_fields(self):
        self.university()
        context=main.build_rubric_input('pt')['precision_teaching_context']
        contracts={
            'precision_diagnostic_task_design':'diagnostic-task-input.schema.json',
            'solo_task_rubric_builder':'task-rubric-input.schema.json',
            'precision_diagnostic_student_ai_dialogue':'dialogue-session-input.schema.json',
            'solo_student_diagnosis_feedback':'diagnosis-input.schema.json',
            'solo_class_diagnosis_intervention':'class-diagnosis-input.schema.json',
            'precision_intervention_goal_path_design':'goal-path-input.schema.json',
            'precision_intervention_activity_formative_regulation':'activity-formative-input.schema.json',
            'precision_intervention_plan_integration_review':'plan-integration-input.schema.json',
        }
        for key,filename in contracts.items():
            schema=json.loads((load_skill(key).path.parent/'assets'/filename).read_text(encoding='utf-8'))
            context_schema=schema['properties']['precision_teaching_context']
            if '$ref' in context_schema:
                context_schema=schema['$defs'][context_schema['$ref'].split('/')[-1]]
            with self.subTest(skill=key):jsonschema.validate(context,context_schema)

    def test_university_prompt_reaches_teacher_and_student_models(self):
        for key in ('precision_diagnostic_task_design','solo_task_rubric_builder','solo_student_diagnosis_feedback','solo_class_diagnosis_intervention','class_teaching_suggestions','precision_intervention_goal_path_design','precision_intervention_activity_formative_regulation','precision_intervention_plan_integration_review'):
            prompt=_skill_runtime_instructions(load_skill(key))
            self.assertIn('大学课堂强制压成45分钟',prompt)
            self.assertIn('课程大纲、课程目标',prompt)
            self.assertIn('不得仅因学生属于大学就预设其SOLO层级较高',prompt)
        prompt=_runtime_instructions(load_skill('precision_diagnostic_student_ai_dialogue'),{})
        self.assertIn('高等教育',prompt)
        self.assertIn('不因为大学生',prompt)

    def test_student_feedback_no_longer_forces_school_audience(self):
        skill=load_skill('solo_student_diagnosis_feedback')
        result=json.loads((skill.path.parent/'tests/sample-output.json').read_text(encoding='utf-8'))
        with patch('backend.app.services.ai._run_structured_skill',return_value={'solo_student_diagnosis':result,'student_feedback_markdown':'大学课程反馈'}) as model:
            run_student_diagnosis_report(skill,{},'openrouter')
        suffix=model.call_args.kwargs['instruction_suffix']
        self.assertIn('大学课程的必要专业表达',suffix)
        self.assertNotIn('面向初高中学生',suffix)

    def test_school_payload_shape_unchanged(self):
        self.assertEqual(set(design._source('pt')[0]['precision_teaching_context']),{'subject','grade','textbook_version_and_chapter','precision_teaching_topic','precision_teaching_goals','precision_teaching_content'})

    def test_90_and_100_minutes_not_changed_to_45(self):
        from backend.app.goal_path_api import TeacherInstructionalContext
        for minutes in (50,90,100):
            context=TeacherInstructionalContext(teaching_content_and_curriculum_analysis='课程大纲：比较理论',teaching_focus_and_difficulty_analysis='证据与解释',planned_duration_minutes=minutes,teaching_environment_and_ai_support_conditions='研讨教室，个人设备')
            self.assertEqual(context.planned_duration_minutes,minutes)
