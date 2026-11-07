import json
import unittest
from unittest.mock import patch

import jsonschema
from backend.app.services import ai
from backend.app.services.skills import load_skill


class StudentReportSchemaRefsTest(unittest.TestCase):
    def test_nested_diagnosis_references_resolve_in_provider_envelope(self):
        skill = load_skill('solo_student_diagnosis_feedback')
        example = json.loads((skill.path.parent / 'tests/sample-output.json').read_text(encoding='utf-8'))
        def generate(*args, **kwargs):
            envelope = kwargs['schema_override']
            self.assertIn('evidenceItem', envelope['$defs'])
            self.assertNotIn('$id', envelope['properties']['solo_student_diagnosis'])
            result = {'solo_student_diagnosis': example, 'student_feedback_markdown': '反馈' * 300}
            jsonschema.validate(result, envelope)
            return result
        with patch.object(ai, '_run_structured_skill', side_effect=generate):
            result, _, feedback = ai.run_student_diagnosis_report(skill, {}, 'openrouter')
        self.assertEqual(result, example)
        self.assertEqual(len(feedback), 600)
