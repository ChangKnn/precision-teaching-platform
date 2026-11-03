"""University teacher analysis uses course context instead of a missing subject."""
import json
from dataclasses import replace
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from backend import test_higher_education as higher_tests
from backend.app import goal_path_api as api
from backend.app.services import ai
from backend.app.services.education_context import education_source_note, higher_education_fields

class UniversityTeacherAnalysisTest(TestCase):
    setUp = higher_tests.HigherEducationTest.setUp
    university = higher_tests.HigherEducationTest.university

    def test_teacher_analysis_endpoint_sends_university_course_and_major(self):
        self.university()
        root=Path(__file__).parent/'skills/precision-intervention-goal-path-design/tests'
        sample=json.loads((root/'sample-input.json').read_text(encoding='utf-8'))['diagnosis_bundle']['class_diagnosis']
        report={'generated_json':json.dumps(sample),'source_hash':'sample'}
        draft={'content_analysis':'课程案例分析','focus_analysis':'理论与证据联系','source_note':'输入中的“学科”为空。课程大纲请核对。'}
        with patch.object(api,'_source_report',return_value=report), patch.object(api,'_analysis_draft',return_value={}), patch.object(api,'run_goal_path_teacher_analysis',return_value=draft) as model:
            api.generate_teacher_analysis('pt','class')
        context=model.call_args.args[0]['precision_teaching']
        self.assertEqual(context['education_stage'],'高等教育')
        self.assertEqual(context['course_name'],'教育心理学')
        self.assertEqual(context['major'],'学前教育')
        self.assertEqual(context['subject'],'')

    def test_old_note_cleanup_preserves_actual_course_basis_limit(self):
        teaching=self.university()
        note='未提供课程大纲原文，请教师核对。输入中的“学科”为空，教材信息包含教育与人的发展。'
        self.assertEqual(education_source_note(teaching,note),'未提供课程大纲原文，请教师核对。')
        self.assertEqual(education_source_note({'grade':'高一','education_stage':'高中'},note),note)
        self.assertEqual(higher_education_fields({'grade':'研二'})['education_stage'],'高等教育')
        self.assertEqual(higher_education_fields({'grade':'博三'})['education_stage'],'高等教育')

    def test_teacher_analysis_prompt_loads_shared_university_rule(self):
        response={'choices':[{'message':{'content':json.dumps({'content_analysis':'课程内容','focus_analysis':'分析联系','source_note':'请核对课程大纲'})}}]}
        with patch.object(ai,'settings',replace(ai.settings,ai_api_key='isolated-test-key',ai_model='test-model')), patch.object(ai,'_post_json',return_value=response) as model:
            ai.run_goal_path_teacher_analysis({'precision_teaching':{'education_stage':'高等教育','subject':'','course_name':'教育心理学'}},'openrouter')
        instructions=model.call_args.args[1]['messages'][0]['content']
        self.assertIn('subject为空属于正常情况',instructions)
        self.assertIn('不得将其视为资料缺失',instructions)
