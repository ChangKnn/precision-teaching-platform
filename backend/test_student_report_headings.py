"""Three-part feedback must not depend on a separate teacher report or diagram."""
import json
import unittest
from unittest.mock import patch
from backend.app.services.ai import run_student_diagnosis_report
from backend.app.services.skills import load_skill
from jsonschema import ValidationError, validate

class StudentReportHeadingsTest(unittest.TestCase):
    def setUp(self):
        self.skill=load_skill('solo_student_diagnosis_feedback')
        self.result=json.loads((self.skill.path.parent/'tests/sample-output.json').read_text(encoding='utf-8'))

    def run_report(self,**extra):
        envelope={'solo_student_diagnosis':self.result,'student_feedback_markdown':'学生易读反馈。'*100,**extra}
        with patch('backend.app.services.ai._run_structured_skill',return_value=envelope) as model:
            result=run_student_diagnosis_report(self.skill,{},'openrouter')
        return result,model.call_args.kwargs

    def test_no_teacher_markdown_is_required(self):
        result,_=self.run_report()
        self.assertEqual(result[0],self.result)
        self.assertEqual(result[2],'学生易读反馈。'*100)

    def test_only_the_three_feedback_sections_are_rendered(self):
        result,_=self.run_report()
        import re
        self.assertEqual(re.findall(r'^## (.+)$',result[1],re.M),['当前情况','进阶目标','学习建议'])
        for label in ['SOLO 水平','判断依据','当前障碍']:self.assertIn('### '+label,result[1])

    def test_no_diagram_markers_are_required(self):
        result,_=self.run_report()
        for marker in ['●','◐','○']:self.assertNotIn(marker,result[1])

    def test_model_contract_requests_no_teacher_report(self):
        _,kwargs=self.run_report()
        schema=kwargs['schema_override']
        self.assertEqual(set(schema['required']),{'solo_student_diagnosis','student_feedback_markdown'})
        self.assertNotIn('teacher_report_markdown',schema['properties'])
        validate({'solo_student_diagnosis':self.result,'student_feedback_markdown':'反馈内容。'*120},schema)

    def test_missing_diagnosis_is_still_rejected(self):
        _,kwargs=self.run_report()
        with self.assertRaises(ValidationError):validate({'student_feedback_markdown':'反馈内容。'*120},kwargs['schema_override'])

    def test_invalid_solo_level_is_still_rejected(self):
        self.result['diagnosis']['level']='invalid'
        with self.assertRaises(ValidationError):self.run_report()

    def test_obsolete_teacher_text_is_not_used_as_a_fallback(self):
        result,_=self.run_report(teacher_report_markdown='只有一句话，没有旧图示或六部分。')
        self.assertNotIn('只有一句话',result[1])
        self.assertIn(self.result['progression']['concrete_goal'],result[1])
