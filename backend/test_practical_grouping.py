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
    def test_avoids_one_and_two_students_when_feasible_and_covers_everyone(self):
        for count in range(1, 61):
            with self.subTest(count=count):
                original = make_report(count)
                snapshot = copy.deepcopy(original)
                result = practical_grouping(original)
                groups = result['grouping_recommendations']['homogeneous_groups']
                ids = [student['student_id'] for group in groups for student in group['students']]
                self.assertEqual(len(ids),count)
                self.assertEqual(len(set(ids)),count)
                if count >= 3:
                    self.assertTrue(all(3 <= len(g['students']) <= 6 for g in groups))
                elif count == 2:
                    self.assertEqual([len(group['students']) for group in groups], [2])
                self.assertEqual(original,snapshot)

    def test_mixed_needs_are_not_described_as_shared(self):
        result = practical_grouping(make_report(4))
        for group in result['grouping_recommendations']['homogeneous_groups']:
            self.assertIn('不同学习需要', group['common_characteristics'])
            for student in group['students']:
                self.assertIn('任务支持' + student['student_id'][1:], group['progression_direction'])

    def test_invalid_roster_is_not_guessed_or_repaired(self):
        report = make_report(4)
        report['grouping_recommendations']['homogeneous_groups'][1]['students'] = copy.deepcopy(report['grouping_recommendations']['homogeneous_groups'][0]['students'])
        self.assertEqual(practical_grouping(report), report)

    def test_already_practical_groups_are_not_changed(self):
        report = make_report(6)
        groups = report['grouping_recommendations']['homogeneous_groups']
        groups[0]['students'] = [g['students'][0] for g in groups]
        report['grouping_recommendations']['homogeneous_groups'] = groups[:1]
        self.assertEqual(practical_grouping(report), report)

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
