import copy
import json
import unittest

from backend.app.services.class_report import validate_class_result
from backend.app.services.compact_outputs import class_transport_schema
from backend.app.services.skills import load_skill


class ClassFeedbackDetailsTest(unittest.TestCase):
    def setUp(self):
        path = load_skill('solo_class_diagnosis_intervention').path.parent
        self.result = json.loads((path / 'tests/sample-output.json').read_text())
        self.payload = json.loads((path / 'tests/sample-input.json').read_text())
        self.schema = json.loads((path / 'assets/class-diagnosis-output.schema.json').read_text())
        self.student = self.payload['student_diagnosis_results'][0]
        self.student['student_statements'] = ['我想继续试试，但是现在有点着急。']
        self.student['submitted_text'] = ''

    def test_new_model_contract_supports_details_and_old_reports(self):
        import jsonschema
        jsonschema.validate(self.result, self.schema)
        compact = class_transport_schema(self.schema)
        self.assertIn('individual_attention', compact['properties'])
        self.assertIn('common_issues', compact['properties'])
        self.assertIn('difficulty_groups', compact['$defs']['levelAnalysis']['properties'])

    def test_attention_evidence_must_belong_to_student(self):
        sid = self.student['student']['student_id']
        self.result['individual_attention'] = [{'student_id':sid,'category':'情绪表达',
            'observation':'表示着急','evidence_quote':'有点着急','teacher_action':'了解需要'}]
        validate_class_result(copy.deepcopy(self.result), self.payload)
        self.result['individual_attention'][0]['evidence_quote'] = '编造的原话'
        with self.assertRaisesRegex(ValueError, '未匹配'):
            validate_class_result(copy.deepcopy(self.result), self.payload)

    def test_difficulty_cannot_reference_another_level(self):
        level = self.result['level_analyses'][0]
        other = next(s for s in self.payload['student_diagnosis_results'] if s['diagnosis']['level'] != level['level'])
        level['difficulty_groups'] = [{'difficulty':'困难','student_ids':[other['student']['student_id']],'teaching_suggestion':'支持'}]
        with self.assertRaisesRegex(ValueError, '不属于该层级'):
            validate_class_result(self.result, self.payload)
