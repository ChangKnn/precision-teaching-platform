import copy
import json
import unittest

from backend.app.services.class_report import validate_class_result, with_student_case_summaries
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

    def test_old_and_partially_classified_reports_use_individual_obstacles(self):
        level = self.result['level_analyses'][0]
        level.pop('difficulty_groups', None)
        view = with_student_case_summaries(self.result, self.payload)
        recovered = view['level_analyses'][0]['recovered_difficulty_groups']
        expected = {member['student_id'] for member in level['students']}
        self.assertEqual({sid for group in recovered for sid in group['student_ids']}, expected)
        for group in recovered:
            for sid in group['student_ids']:
                student = next(s for s in self.payload['student_diagnosis_results'] if s['student']['student_id'] == sid)
                self.assertEqual(group['problem_description'], student['progression']['main_obstacle'])
        first = level['students'][0]['student_id']
        level['difficulty_groups'] = [{'difficulty':'已有分类','student_ids':[first],'teaching_suggestion':'支持'}]
        view = with_student_case_summaries(self.result, self.payload)
        recovered_ids = {sid for group in view['level_analyses'][0]['recovered_difficulty_groups'] for sid in group['student_ids']}
        self.assertEqual(recovered_ids, expected - {first})
