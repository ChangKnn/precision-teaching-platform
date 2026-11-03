"""Activity scope uses goal references, with goal membership kept separately."""
import copy
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from jsonschema import validate, ValidationError
from backend.app.services.activity_scope import without_activity_rosters
from backend.app.services.ai import _skill_runtime_instructions, run_activity_formative_design
from backend.app.services.skills import load_skill
from backend.app.goal_path_api import _markdown, _validate_design

ROOT = Path(__file__).parent / 'skills'


class ActivityScopeTest(TestCase):
    def setUp(self):
        self.folder = ROOT / 'precision-intervention-goal-path-design'
        self.result = json.loads((self.folder / 'tests/sample-output.json').read_text(encoding='utf-8'))
        self.source = json.loads((self.folder / 'tests/sample-input.json').read_text(encoding='utf-8'))

    def test_legacy_view_removes_nested_rosters_without_mutation(self):
        original = {'progression_goals': self.result['progression_goals'], 'activities': [{
            'target_student_ids': ['old'], 'target_goal_ids': ['PG1'],
            'parallel_group_tasks': [{'target_students': [{'student_id': 'old'}], 'group_name': '解释组'}],
        }], 'formative_evaluation_nodes': [{'target_student_ids': ['old'], 'target_goal_ids': ['PG1']}]}
        before = copy.deepcopy(original)
        clean = without_activity_rosters(original)
        self.assertEqual(original, before)
        self.assertEqual(clean['progression_goals'], original['progression_goals'])
        self.assertNotIn('target_student_ids', clean['activities'][0])
        self.assertNotIn('target_students', clean['activities'][0]['parallel_group_tasks'][0])
        self.assertNotIn('target_student_ids', clean['formative_evaluation_nodes'][0])
        self.assertEqual(clean['activities'][0]['target_goal_ids'], ['PG1'])

    def test_new_goal_schema_rejects_activity_roster_but_legacy_validation_reads_it(self):
        schema = json.loads((self.folder / 'assets/goal-path-output.schema.json').read_text(encoding='utf-8'))
        legacy = copy.deepcopy(self.result)
        legacy['intervention_path']['stages'][0]['activity_units'][0]['target_students'] = [{'student_id': 'obsolete'}]
        with self.assertRaises(ValidationError):
            validate(legacy, schema)
        _validate_design(legacy, self.source['diagnosis_bundle']['class_diagnosis'], 45)

    def test_confirmation_report_shows_only_concrete_pending_decisions(self):
        self.result['teacher_confirmation'] = {'items_to_confirm': ['generic checklist'], 'unresolved_decisions': ['请确认投影设备是否可用。']}
        report = _markdown(self.result)
        self.assertIn('请确认投影设备是否可用。', report)
        self.assertNotIn('generic checklist', report)
        self.result['teacher_confirmation']['unresolved_decisions'] = []
        self.assertNotIn('请教师确认', _markdown(self.result))

    def test_runtime_contains_short_examples_and_both_output_modes(self):
        prompt = _skill_runtime_instructions(load_skill('precision_intervention_goal_path_design'))
        for text in ('历史：', '数学：', 'changes', '首次', '学生名单'):
            self.assertIn(text, prompt)

    def test_example_symbolic_path_matches_actual_stages(self):
        path = self.result['intervention_path']
        parts = []
        for stage in path['stages']:
            units = stage['activity_units']
            codes = [unit['organization_code'] for unit in units]
            if len(units) == 1:
                parts.append(codes[0])
            else:
                counts = {}
                numbered = []
                for code in codes:
                    counts[code] = counts.get(code, 0) + 1
                    numbered.append(f'{code}{counts[code]}')
                parts.append('{' + ' | '.join(numbered) + '}')
        self.assertIn(' → '.join(parts), json.dumps(path, ensure_ascii=False))

    def test_activity_model_receives_no_legacy_participants(self):
        folder = ROOT / 'precision-intervention-activity-formative-regulation'
        payload = json.loads((folder / 'tests/sample-input.json').read_text(encoding='utf-8'))
        output = json.loads((folder / 'tests/sample-output.json').read_text(encoding='utf-8'))
        unit = payload['goal_path_design']['intervention_path']['primary']['stages'][0]['activity_units'][0]
        unit['target_student_ids'] = ['old']
        with patch('backend.app.services.ai._run_structured_skill', return_value=output) as model:
            run_activity_formative_design(load_skill('precision_intervention_activity_formative_regulation'), payload, 'openrouter')
        sent = model.call_args.args[1]
        self.assertNotIn('target_student_ids', sent['goal_path_design']['intervention_path']['primary']['stages'][0]['activity_units'][0])
        self.assertEqual(sent['goal_path_design']['progression_goals'], payload['goal_path_design']['progression_goals'])
        self.assertIn('target_student_ids', unit)
