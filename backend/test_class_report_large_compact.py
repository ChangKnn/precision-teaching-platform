import copy
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from backend.app.services import ai
from backend.app.services.class_report import validate_class_result
from backend.app.services.skills import load_skill


class LargeClassCompactTest(unittest.TestCase):
    def test_business_validation_failure_keeps_stage_and_reason_after_repair(self):
        skill = load_skill('solo_class_diagnosis_intervention')
        schema = {'type':'object','properties':{}}
        response = {'choices':[{'message':{'content':'{}'}}]}
        with patch.object(ai, 'settings', replace(ai.settings, ai_api_key='test-key', ai_model='test-model')), patch.object(ai, '_post_json', return_value=response) as post, patch.object(ai, '_validate_class_problem_groups', side_effect=ValueError('M层级问题须关联本层级真实学生')):
            with self.assertRaisesRegex(RuntimeError, '分层分析：M层级问题须关联本层级真实学生'):
                ai._run_rubric_compatible(skill, {}, schema, 'class_level_problems', '')
        self.assertEqual(post.call_count, 2)

    def test_business_error_does_not_echo_unexpected_student_data(self):
        detail = ai._class_validation_diagnostic(ValueError('unexpected private conversation'), 'class_overview')
        self.assertNotIn('private conversation', detail)
        self.assertIn('班级概览', detail)

    def test_overview_retries_invented_common_issue_student_id(self):
        from backend.app.services.compact_outputs import class_transport_schema
        skill = load_skill('solo_class_diagnosis_intervention')
        path = skill.path.parent
        payload = json.loads((path / 'tests/sample-input.json').read_text())
        schema = json.loads((path / 'assets/class-diagnosis-output.schema.json').read_text())
        output = json.loads((path / 'tests/sample-output.json').read_text())
        for key in ('class_summary', 'traceability', 'grouping_recommendations'):
            output.pop(key)
        for row in output['level_analyses']:
            row.pop('students'); row.pop('count')
            for evidence in row['typical_evidence']:
                for key in ('student_name', 'quote', 'interpretation'):
                    evidence.pop(key)
        valid_id = payload['student_diagnosis_results'][0]['student']['student_id']
        output['common_issues'] = [{'issue':'依据需要补充','basis_student_ids':[valid_id]}]
        invalid = copy.deepcopy(output)
        invalid['common_issues'][0]['basis_student_ids'] = ['invented-student']
        response = lambda value: {'choices':[{'message':{'content':json.dumps(value, ensure_ascii=False)}}]}
        with patch.object(ai, 'settings', replace(ai.settings, ai_api_key='test-key', ai_model='test-model')), patch.object(ai, '_post_json', side_effect=[response(invalid), response(output)]) as post:
            result = ai._run_rubric_compatible(skill, payload, class_transport_schema(schema, compact_large=True, separate_problems=True), 'class_overview', '')
        self.assertEqual(result['common_issues'][0]['basis_student_ids'], [valid_id])
        self.assertEqual(post.call_count, 2)
        self.assertIn('未知学生', post.call_args_list[1].args[1]['messages'][-1]['content'])

    def test_unverified_common_issue_is_omitted_after_retry(self):
        from backend.app.services.compact_outputs import class_transport_schema
        skill = load_skill('solo_class_diagnosis_intervention')
        path = skill.path.parent
        payload = json.loads((path / 'tests/sample-input.json').read_text())
        schema = json.loads((path / 'assets/class-diagnosis-output.schema.json').read_text())
        output = json.loads((path / 'tests/sample-output.json').read_text())
        for key in ('class_summary', 'traceability', 'grouping_recommendations'):
            output.pop(key)
        for row in output['level_analyses']:
            row.pop('students'); row.pop('count')
            for evidence in row['typical_evidence']:
                for key in ('student_name', 'quote', 'interpretation'):
                    evidence.pop(key)
        valid_id = payload['student_diagnosis_results'][0]['student']['student_id']
        output['common_issues'] = [
            {'issue':'有部分可核对的依据','basis_student_ids':[valid_id, 'unknown']},
            {'issue':'完全无法核对','basis_student_ids':['unknown']},
        ]
        response = {'choices':[{'message':{'content':json.dumps(output, ensure_ascii=False)}}]}
        with patch.object(ai, 'settings', replace(ai.settings, ai_api_key='test-key', ai_model='test-model')), patch.object(ai, '_post_json', return_value=response) as post:
            result = ai._run_rubric_compatible(skill, payload, class_transport_schema(schema, compact_large=True, separate_problems=True), 'class_overview', '')
        self.assertEqual(post.call_count, 2)
        self.assertEqual(result['common_issues'], [{'issue':'有部分可核对的依据','basis_student_ids':[valid_id]}])
        self.assertIn('未通过核验', result['common_issue_warning'])

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
                answer = {'level_analyses':[{'level':level,'level_group_name':'信息比较','main_performance':'学生能识别部分相关信息，但对联系仍需要支持。', 'teaching_design':{'content':'提供相关材料','task':'比较信息','scaffold':'给出观察提示'},'difficulty_groups':grouped[level]}]}
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
        import jsonschema
        from backend.app.services.compact_outputs import practical_grouping
        from backend.app.services.class_report import render_class_markdown
        jsonschema.validate(result, json.loads((path / 'assets/class-diagnosis-output.schema.json').read_text()))
        self.assertEqual(result['grouping_recommendations'], practical_grouping(result)['grouping_recommendations'])
        self.assertTrue(all(group['group_name'] == '信息比较组' for group in groups))
        markdown = render_class_markdown(result)
        self.assertIn('学习任务：比较信息', markdown)
        self.assertIn('教学支持：', markdown)


    def test_class_skill_receives_teacher_provided_progression_reference(self):
        instructions = ai._skill_runtime_instructions(load_skill('solo_class_diagnosis_intervention'))
        self.assertIn('教师提供的SOLO进阶设计依据', instructions)
        self.assertIn('关键认知变化→学习内容→学习活动→支架', instructions)
        self.assertIn('数量、空间、逻辑、实验、因果或文本关系', instructions)
        self.assertNotIn('东汉', instructions)

    def test_different_subject_tasks_are_forwarded_without_replacement(self):
        skill = load_skill('solo_class_diagnosis_intervention')
        path = skill.path.parent
        schema = json.loads((path / 'assets/class-diagnosis-output.schema.json').read_text())
        initial = json.loads((path / 'tests/sample-input.json').read_text())
        sample = json.loads((path / 'tests/sample-output.json').read_text())
        overview = copy.deepcopy(sample)
        for key in ('class_summary', 'traceability', 'grouping_recommendations'):
            overview.pop(key)
        for row in overview['level_analyses']:
            row.pop('students'); row.pop('count')
            for evidence in row['typical_evidence']:
                for key in ('student_name', 'quote', 'interpretation'):
                    evidence.pop(key)
        for subject, task in [('历史', '分析材料中的多种解释'), ('数学', '比较两种求解方法'), ('大学课程', '分析模型的适用条件')]:
            with self.subTest(subject=subject):
                payload = copy.deepcopy(initial)
                payload['precision_teaching_context']['subject'] = subject
                payload['diagnostic_task_text'] = task
                def fake_run(skill_arg, input_arg, schema_arg, output_name, suffix):
                    if output_name == 'class_overview':
                        return copy.deepcopy(overview)
                    self.assertEqual(input_arg['precision_teaching_context']['subject'], subject)
                    self.assertEqual(input_arg['diagnostic_task_text'], task)
                    level = input_arg['student_diagnosis_results'][0]['diagnosis']['level']
                    ids = [s['student']['student_id'] for s in input_arg['student_diagnosis_results']]
                    return {'level_analyses':[{'level':level,'main_performance':'基于学生回应的表现。',
                        'teaching_design':{'content':'当前课程材料','task':task,'scaffold':'观察提示'},
                        'difficulty_groups':[{'problem_label':'解释不足','problem_description':'说明尚不充分。',
                                              'student_ids':ids,'teaching_suggestion':'根据材料追问。'}]}]}
                with patch.object(ai, '_run_rubric_compatible', side_effect=fake_run):
                    result, _ = ai._run_large_class_in_stages(skill, payload, 'openrouter', schema)
                self.assertTrue(all(row['teaching_design']['task'] == task for row in result['level_analyses']))

    def test_unknown_evidence_reference_is_rejected(self):
        from backend.app.services.compact_outputs import expand_large_class_output
        with self.assertRaisesRegex(ValueError, '未匹配'):
            expand_large_class_output({'level_analyses':[{'typical_evidence':[{'student_id':'wrong','source_ref':'bad'}]}]}, {'student_diagnosis_results':[]})
