"""Teacher descriptions must be readable while internal trace IDs remain usable."""
import copy
import json
import unittest
from unittest.mock import patch

import jsonschema

from backend.app.services.ai import _mock_task_rubric, validate_task_rubric, run_skill
from backend.app.services.skills import load_skill


class RubricTeacherLanguageTest(unittest.TestCase):
    def setUp(self):
        self.skill = load_skill('solo_task_rubric_builder')
        self.rubric = _mock_task_rubric({
            'precision_teaching_context': {'precision_teaching_topic': '教学设计论证能力'},
            'diagnostic_task': '比较两项教学设计，结合目标和学生表现说明理由。',
        })

    def test_internal_traceability_remains_valid(self):
        validate_task_rubric(self.skill, self.rubric)
        self.assertIn('E1', self.rubric['levels']['R']['trace_to'])

    def test_readable_descriptions_accepted(self):
        rubric = copy.deepcopy(self.rubric)
        rubric['levels']['R']['decision_evidence'] = ['结合教学目标和学生表现说明方案选择理由，并解释如何检验设计效果。']
        validate_task_rubric(self.skill, rubric)

    def test_internal_codes_rejected_in_teacher_fields(self):
        for field in ('core_performance', 'decision_evidence', 'adjacent_boundaries', 'typical_expressions', 'limitations'):
            for code in ('E1', 'E2', 'R1', 'A1', 'PATH-1', 'G-R'):
                with self.subTest(field=field, code=code):
                    rubric = copy.deepcopy(self.rubric)
                    rubric['levels']['R'][field] = [f'根据{code}形成判断。']
                    with self.assertRaisesRegex(ValueError, '内部编号'):
                        validate_task_rubric(self.skill, rubric)

    def test_solo_level_names_are_not_internal_task_codes(self):
        rubric = copy.deepcopy(self.rubric)
        rubric['levels']['R']['adjacent_boundaries'] = ['与 M 的区别是形成整体解释；EA 还需概括或迁移。']
        validate_task_rubric(self.skill, rubric)

    def test_design_intent_is_required_before_model_call(self):
        for value in (None, '', '  \n '):
            with self.subTest(value=value), patch('backend.app.services.ai._run_structured_skill') as model:
                payload = {} if value is None else {'diagnostic_task_design_intent': value}
                with self.assertRaisesRegex(ValueError, '诊断任务设计意图'):
                    run_skill(self.skill, payload, 'openrouter')
                model.assert_not_called()

    def test_input_schema_requires_nonblank_design_intent(self):
        root = self.skill.path.parent
        schema = json.loads((root / 'assets/task-rubric-input.schema.json').read_text(encoding='utf-8'))
        sample = json.loads((root / 'tests/sample-input.json').read_text(encoding='utf-8'))
        jsonschema.validate(sample, schema)
        for value in (None, '', '   '):
            payload = copy.deepcopy(sample)
            if value is None:
                del payload['diagnostic_task_design_intent']
            else:
                payload['diagnostic_task_design_intent'] = value
            with self.subTest(value=value), self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(payload, schema)


if __name__ == '__main__':
    unittest.main()
