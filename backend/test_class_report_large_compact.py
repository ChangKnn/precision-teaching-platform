import copy
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from backend.app.services import ai
from backend.app.services.class_report import validate_class_result
from backend.app.services.skills import load_skill


class LargeClassCompactTest(unittest.TestCase):
    def test_large_class_recovers_verified_evidence_and_complete_groups(self):
        skill = load_skill('solo_class_diagnosis_intervention')
        path = skill.path.parent
        payload = json.loads((path / 'tests/sample-input.json').read_text())
        payload['diagnostic_task_text'] = '比较实验中温度对蒸发的影响。'
        output = json.loads((path / 'tests/sample-output.json').read_text())
        first = payload['student_diagnosis_results'][0]
        for i in range(35):
            student = copy.deepcopy(first)
            student['student']['student_id'] = f'extra-{i}'
            student['student']['student_name'] = f'测试{i}'
            student['student_statements'] = ['模拟发言' * 250]
            payload['student_diagnosis_results'].append(student)
        output.pop('class_summary')
        output.pop('traceability')
        output.pop('grouping_recommendations')
        for row in output['level_analyses']:
            row.pop('students')
            row.pop('count')
            ids = [s['student']['student_id'] for s in payload['student_diagnosis_results'] if s['diagnosis']['level'] == row['level']]
            row['difficulty_groups'] = [{'problem_label':'依据不足',
                'problem_description':'尚未结合任务证据说明观点。', 'student_ids':ids, 'teaching_suggestion':'提示学生寻找证据。'}]
            for ev in row['typical_evidence']:
                for k in ('student_name', 'quote', 'interpretation'): ev.pop(k)
        grouped = {row['level']: row.pop('difficulty_groups') for row in output['level_analyses']}
        def fake_post(url, body, key, **kwargs):
            if body['response_format']['json_schema']['name'] == 'class_overview':
                answer = output
            else:
                request = json.loads(body['messages'][1]['content'].split('JSON：\n', 1)[1])
                self.assertEqual(request['diagnostic_task_text'], payload['diagnostic_task_text'])
                self.assertTrue(request['student_diagnosis_results'][0]['diagnosis']['evidence'])
                level = request['student_diagnosis_results'][0]['diagnosis']['level']
                answer = {'level_analyses':[{'level':level,'main_performance':'学生能识别部分相关信息，但对联系仍需要支持。', 'teaching_design':{'content':'提供相关材料','task':'比较信息','scaffold':'给出观察提示'},'difficulty_groups':grouped[level]}]}
            return {'choices':[{'message':{'content':json.dumps(answer,ensure_ascii=False)}}]}
        with patch.object(ai, 'settings', replace(ai.settings, ai_api_key='test-key', ai_model='test-model')), patch.object(ai, '_post_json', side_effect=fake_post) as post:
            result, _ = ai.run_class_diagnosis_report(skill, payload, 'openrouter')
        validate_class_result(result, payload)
        groups = result['grouping_recommendations']['homogeneous_groups']
        all_ids = [student['student_id'] for group in groups for student in group['students']]
        self.assertEqual(len(all_ids), len(payload['student_diagnosis_results']))
        self.assertEqual(len(all_ids), len(set(all_ids)))
        self.assertTrue(all(len(group['students']) <= 6 for group in groups))
        schema = next(call.args[1]['response_format']['json_schema']['schema'] for call in post.call_args_list if call.args[1]['response_format']['json_schema']['name'] == 'class_overview')
        self.assertEqual(post.call_count, len(grouped) + 1)
        self.assertNotIn('grouping_recommendations', schema['properties'])
        self.assertNotIn('quote', schema['$defs']['evidenceRef']['properties'])
        self.assertTrue(result['level_analyses'][0]['typical_evidence'][0]['quote'])
        self.assertTrue(result['level_analyses'][0]['teaching_design']['scaffold'])

    def test_class_skill_receives_teacher_provided_progression_reference(self):
        instructions = ai._skill_runtime_instructions(load_skill('solo_class_diagnosis_intervention'))
        self.assertIn('教师提供的SOLO进阶设计依据', instructions)
        self.assertIn('关键认知变化→学习内容→学习活动→支架', instructions)

    def test_unknown_evidence_reference_is_rejected(self):
        from backend.app.services.compact_outputs import expand_large_class_output
        with self.assertRaisesRegex(ValueError, '未匹配'):
            expand_large_class_output({'level_analyses':[{'typical_evidence':[{'student_id':'wrong','source_ref':'bad'}]}]}, {'student_diagnosis_results':[]})
