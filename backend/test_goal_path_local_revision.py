"""Small teacher edits preserve the existing roster without another full output."""
import copy
import json
from dataclasses import replace
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from backend.app.services import ai
from backend.app.services.skills import load_skill
from backend.app.services.goal_path_revision import apply_goal_path_revision
from backend.app.goal_path_api import _validate_design

ROOT = Path(__file__).parent / 'skills/precision-intervention-goal-path-design/tests'


class LocalGoalPathRevisionTest(TestCase):
    def setUp(self):
        self.previous = json.loads((ROOT / 'sample-output.json').read_text(encoding='utf-8'))
        self.source = json.loads((ROOT / 'sample-input.json').read_text(encoding='utf-8'))

    def change(self, path, value):
        return {'path': path, 'value': value}

    def validate(self, result):
        _validate_design(result, self.source['diagnosis_bundle']['class_diagnosis'], 45)

    def test_small_text_edit_keeps_all_unrelated_content(self):
        before = copy.deepcopy(self.previous)
        change = self.change('/intervention_path/stages/1/stage_name', '解释实验结果')
        actual = apply_goal_path_revision(before, {'changes': [change]})
        expected = copy.deepcopy(before)
        expected['intervention_path']['stages'][1]['stage_name'] = '解释实验结果'
        self.assertEqual(actual, expected)
        self.assertEqual(before, self.previous)
        self.validate(actual)

    def test_existing_array_replacement_allows_requested_regrouping(self):
        expected = copy.deepcopy(self.previous)
        first, second, third = expected['progression_goals']
        moved = second['target_students'].pop()
        third['target_students'].append(moved)
        third['source_levels'] = ['M', 'R']
        next(a for a in expected['student_goal_assignments'] if a['student_id'] == moved['student_id'])['primary_goal_id'] = third['goal_id']
        revision = {'changes': [self.change('/'+key, expected[key]) for key in ['progression_goals','student_goal_assignments']]}
        self.validate(apply_goal_path_revision(self.previous, revision))

    def test_invalid_roster_is_still_rejected(self):
        changed = apply_goal_path_revision(self.previous, {'changes': [self.change('/progression_goals/0/target_students/0/student_id', 'unknown')]})
        with self.assertRaises(ValueError):
            self.validate(changed)
        self.validate(self.previous)

    def test_invalid_and_overlapping_paths_do_not_mutate_original(self):
        before = copy.deepcopy(self.previous)
        for changes in [
            [self.change('/missing', 'x')],
            [self.change('/intervention_path/stages/99/stage_name', 'x')],
            [self.change('/teacher_confirmation/status', 'teacher_confirmed')],
            [self.change('/intervention_path', {}), self.change('/intervention_path/stages', [])],
            [self.change('/schema_version', '99')],
        ]:
            with self.assertRaises(ValueError):
                apply_goal_path_revision(before, {'changes': changes})
            self.assertEqual(before, self.previous)

    def test_revision_model_returns_only_changes_and_complete_plan_is_validated(self):
        skill = load_skill('precision_intervention_goal_path_design')
        revision = {'changes': [self.change('/intervention_path/stages/1/stage_name', '解释实验结果')]}
        payload = {'previous_goal_path_design': self.previous, 'teacher_revision_request': '只改第二个活动名称'}
        with patch.object(ai, '_run_structured_skill', return_value=revision) as model:
            actual, text = ai.run_goal_path_design(skill, payload, 'openrouter')
        self.assertIsNone(text)
        self.assertEqual(actual['student_goal_assignments'], self.previous['student_goal_assignments'])
        self.assertEqual(model.call_args.kwargs['schema_override']['required'], ['changes'])
        self.validate(actual)

    def test_empty_change_preserves_original_plan(self):
        self.assertEqual(apply_goal_path_revision(self.previous, {'changes': []}), self.previous)

    def test_native_text_needs_no_second_json_decoding(self):
        for value in ['案例导入', '比较“事实”与“推断”\n补充依据', '路径 C:\\课堂\\材料', 'true', '123']:
            revision = {'changes': [self.change('/intervention_path/stages/1/stage_name', value)]}
            result = apply_goal_path_revision(self.previous, json.loads(json.dumps(revision)))
            self.assertEqual(result['intervention_path']['stages'][1]['stage_name'], value)
            self.validate(result)

    def test_native_values_keep_types_and_are_not_shared_with_response(self):
        revision = {'changes': [
            self.change('/intervention_path/stages/0/duration_minutes', 6),
            self.change('/progression_goals/0/custom_goal_note', None),
            self.change('/teacher_confirmation/unresolved_decisions', ['需核对材料']),
        ]}
        result = apply_goal_path_revision(self.previous, revision)
        self.assertIs(type(result['intervention_path']['stages'][0]['duration_minutes']), int)
        self.assertIsNone(result['progression_goals'][0]['custom_goal_note'])
        revision['changes'][2]['value'].append('不应影响合并结果')
        self.assertEqual(result['teacher_confirmation']['unresolved_decisions'], ['需核对材料'])

    def test_wrong_field_type_is_rejected_by_complete_plan_schema(self):
        skill = load_skill('precision_intervention_goal_path_design')
        payload = {'previous_goal_path_design': self.previous, 'teacher_revision_request': '改名称'}
        revision = {'changes': [self.change('/intervention_path/stages/1/stage_name', {'bad': 'type'})]}
        from jsonschema import ValidationError
        before = copy.deepcopy(self.previous)
        with patch.object(ai, '_run_structured_skill', return_value=revision), self.assertRaises(ValidationError):
            ai.run_goal_path_design(skill, payload, 'openrouter')
        self.assertEqual(self.previous, before)

    def test_actual_model_request_uses_revision_schema(self):
        revision = {'changes': [self.change('/intervention_path/stages/1/stage_name', '解释实验结果')]}
        response = {'choices': [{'message': {'content': json.dumps(revision)}}]}
        payload = {'previous_goal_path_design': self.previous, 'teacher_revision_request': '只改第二个活动名称'}
        with patch.object(ai, 'settings', replace(ai.settings, ai_api_key='test-only', ai_model='synthetic')), patch.object(ai, '_post_json', return_value=response) as model:
            actual, _ = ai.run_goal_path_design(load_skill('precision_intervention_goal_path_design'), payload, 'openrouter')
        body = model.call_args.args[1]
        self.assertEqual(body['response_format']['json_schema']['schema']['required'], ['changes'])
        self.assertIn('只修改teacher_revision_request要求涉及的内容', body['messages'][0]['content'])
        self.assertEqual(actual['student_goal_assignments'], self.previous['student_goal_assignments'])
        self.validate(actual)
