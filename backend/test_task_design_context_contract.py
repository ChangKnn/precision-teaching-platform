"""Exercise the complete task-design validation path without a paid model call."""
import copy
import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.services.ai import run_diagnostic_task_design
from backend.app.services.skills import load_skill


ROOT = Path(__file__).parent / 'skills/precision-diagnostic-task-design'
spec = importlib.util.spec_from_file_location('task_context_contract_test', ROOT / 'scripts/validate_contract.py')
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


class TaskContextContractTest(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads((ROOT / 'tests/sample-input.json').read_text(encoding='utf-8'))
        self.output = json.loads((ROOT / 'tests/sample-output.json').read_text(encoding='utf-8'))

    def university(self):
        payload = copy.deepcopy(self.payload)
        payload['precision_teaching_context'].update(education_stage='高等教育', grade='大三',
            course_name='中学教育学基础', major='教育学')
        return payload

    def test_school_context_still_valid(self):
        self.assertEqual(contract.validate_input(self.payload), [])

    def test_university_optional_fields_valid(self):
        self.assertEqual(contract.validate_input(self.university()), [])

    def test_university_complete_generation_validation(self):
        with patch('backend.app.services.ai._run_structured_skill', return_value=self.output):
            result = run_diagnostic_task_design(load_skill('precision_diagnostic_task_design'), self.university(), 'openrouter')
        self.assertEqual(len(result['diagnostic_task_design']['candidates']), 3)

    def test_required_fields_not_relaxed(self):
        for key in self.payload['precision_teaching_context']:
            with self.subTest(key=key):
                payload = self.university()
                del payload['precision_teaching_context'][key]
                self.assertTrue(contract.validate_input(payload))

    def test_unknown_fields_rejected(self):
        payload = self.university()
        payload['precision_teaching_context']['unknown_context'] = 'value'
        self.assertTrue(contract.validate_input(payload))

    def test_invalid_optional_values_rejected(self):
        for key, value in [('education_stage', 'other'), ('course_name', []), ('major', '字' * 121)]:
            with self.subTest(key=key):
                payload = self.university()
                payload['precision_teaching_context'][key] = value
                self.assertTrue(contract.validate_input(payload))

    def test_non_object_context_returns_errors(self):
        for value in [None, [], 'text']:
            with self.subTest(value=value):
                self.assertTrue(contract.validate_input({'precision_teaching_context': value}))


if __name__ == '__main__':
    unittest.main()
