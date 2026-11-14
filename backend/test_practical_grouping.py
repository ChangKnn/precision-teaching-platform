import copy
import json
import unittest
from unittest.mock import patch

from backend.app.services.compact_outputs import practical_grouping


def make_report(count):
    levels = ('P', 'U', 'M', 'R', 'EA')
    distribution = [{'level':level, 'students':[]} for level in levels]
    groups = []
    for i in range(count):
        level = i % len(levels)
        student = {'student_id': f's{i}', 'student_name': f'学生{i}'}
        distribution[level]['students'].append(student)
        groups.append({'group_id':f'H{i+1}', 'group_name':f'原组{i}', 'students':[student],
                       'common_characteristics':f'仅学生{i}的具体需要', 'progression_direction':f'任务支持{i}', 'use_case':'课堂'})
    return {'class_summary':{'distribution':distribution}, 'grouping_recommendations':{'homogeneous_groups':groups,
        'heterogeneous_groups':[], 'usage_note':'原说明', 'heterogeneous_not_recommended_reason':'无'}}


class PracticalGroupingTest(unittest.TestCase):
    def test_groups_by_level_and_preserves_all_students(self):
        for count in range(1, 61):
            report = make_report(count)
            original = copy.deepcopy(report)
            result = practical_grouping(report)
            groups = result['grouping_recommendations']['homogeneous_groups']
            levels = {s['student_id']: row['level'] for row in report['class_summary']['distribution'] for s in row['students']}
            ids = [s['student_id'] for g in groups for s in g['students']]
            self.assertEqual(set(ids), set(levels))
            self.assertEqual(len(ids), len(set(ids)))
            self.assertTrue(all(len({levels[s['student_id']] for s in g['students']}) == 1 for g in groups))
            self.assertEqual(report, original)

    def test_large_level_uses_evidenced_difficulties_without_duplicates(self):
        report = make_report(10)
        all_students = [s for row in report['class_summary']['distribution'] for s in row['students']]
        for row in report['class_summary']['distribution']:
            row['students'] = all_students if row['level'] == 'P' else []
        report['level_analyses'] = [{'level':'P', 'main_performance':'整体表现', 'difficulty_groups':[
            {'student_ids':['s0','s1','s2'], 'problem_label':'问题一', 'problem_description':'具体困难', 'teaching_suggestion':'共同支持'},
            {'student_ids':['s2','s3'], 'problem_label':'问题二', 'problem_description':'另一困难', 'teaching_suggestion':'另一支持'}]}]
        groups = practical_grouping(report)['grouping_recommendations']['homogeneous_groups']
        ids = [s['student_id'] for g in groups for s in g['students']]
        self.assertEqual(len(ids), 10)
        self.assertEqual(len(set(ids)), 10)
        self.assertEqual(groups[0]['group_name'], '问题一')
        self.assertEqual(groups[0]['progression_direction'], '共同支持')
        self.assertFalse(any('support_breakdown' in g for g in groups))

    def test_level_name_and_support_are_based_on_analysis(self):
        report = make_report(5)
        report['level_analyses'] = [{'level':'P', 'level_group_name':'线索辨认', 'main_performance':'正在辨认文本中的相关线索。',
            'difficulty_groups':[], 'teaching_design':{'task':'圈画线索', 'scaffold':'提供对照表'}}]
        groups = practical_grouping(report)['grouping_recommendations']['homogeneous_groups']
        self.assertEqual(groups[0]['group_name'], '线索辨认')
        self.assertEqual(groups[0]['progression_direction'], '提供对照表')
        self.assertEqual(groups[1]['group_name'], '单一要素')

    def test_existing_report_view_and_future_goal_design_use_same_grouping(self):
        from backend.app import goal_path_api
        from backend.app.services import class_report, class_teaching_suggestions
        original = make_report(4)
        row = {'teaching_id':'t','classroom_id':'c','status':'ready','generated_json':json.dumps(original),
               'skill_key':'test','skill_version':'1','provider':'openrouter','source_hash':'h',
               'report_text':'旧报告','error':None,'generated_at':None,'updated_at':'t'}
        with patch.object(class_report, 'fetch_one', return_value=row), patch.object(class_teaching_suggestions, 'latest_suggestions', return_value=None):
            displayed = class_report.report_row('t','c')['result']['grouping_recommendations']
        with patch.object(goal_path_api, 'fetch_one', side_effect=[{'ok':1}, row]):
            source = json.loads(goal_path_api._source_report('t','c')['generated_json'])['grouping_recommendations']
        self.assertEqual(displayed, source)
        self.assertEqual(len(json.loads(row['generated_json'])['grouping_recommendations']['homogeneous_groups']), 4)
