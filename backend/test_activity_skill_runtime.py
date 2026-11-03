"""Verify the actual model request receives the classroom activity design rules."""
import json
from dataclasses import replace
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from backend.app.services import ai
from backend.app.services.skills import load_skill


class ActivitySkillRuntimeTest(TestCase):
    def test_model_request_loads_concrete_tasks_and_flexible_support(self):
        skill = load_skill('precision_intervention_activity_formative_regulation')
        samples = skill.path.parent / 'tests'
        source = json.loads((samples / 'sample-input.json').read_text(encoding='utf-8'))
        output = json.loads((samples / 'sample-output.json').read_text(encoding='utf-8'))
        response = {'choices': [{'message': {'content': json.dumps(output)}}]}
        with patch.object(ai, 'settings', replace(ai.settings, ai_api_key='test-only', ai_model='synthetic')), patch.object(ai, '_post_json', return_value=response) as model, patch.object(ai, 'record_llm_request'):
            result = ai.run_activity_formative_design(skill, source, 'openrouter')
        request = model.call_args.args[1]
        instructions = '\n'.join(message['content'] for message in request['messages'] if message['role'] == 'system')
        self.assertEqual(skill.version, '1.3.0')
        self.assertEqual(result, output)
        self.assertIn('小凡案例', instructions)
        self.assertIn('不是学习上限', instructions)
        self.assertIn('实际困难', instructions)
        self.assertIn('材料、问题、操作与产出', instructions)
        self.assertNotIn('| P→U |', instructions)
        self.assertNotIn('括号内标注对应的SOLO层级', instructions)
