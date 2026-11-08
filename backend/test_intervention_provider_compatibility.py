import copy
import json
from dataclasses import replace
from unittest import TestCase
from unittest.mock import patch

from backend.app.services import ai
from backend.app.services.skills import load_skill


class InterventionProviderCompatibilityTest(TestCase):
    def test_grammar_fallback_for_activity_and_plan(self):
        cases = (
            ('precision_intervention_activity_formative_regulation', ai.run_activity_formative_design),
            ('precision_intervention_plan_integration_review', ai.run_intervention_plan_integration),
        )
        for key, run in cases:
            with self.subTest(skill=key):
                skill = load_skill(key)
                samples = skill.path.parent / 'tests'
                source = json.loads((samples / 'sample-input.json').read_text(encoding='utf-8'))
                expected = json.loads((samples / 'sample-output.json').read_text(encoding='utf-8'))
                requests = []

                def post(url, body, api_key, **kwargs):
                    requests.append(copy.deepcopy(body))
                    if len(requests) == 1:
                        raise RuntimeError('模型请求失败（400）：The compiled grammar is too large')
                    return {'choices': [{'message': {'content': json.dumps(expected)}}]}

                with patch.object(ai, 'settings', replace(ai.settings, ai_api_key='test', ai_model='test')), patch.object(ai, '_post_json', side_effect=post):
                    self.assertEqual(run(skill, source, 'openrouter'), expected)
                self.assertEqual(requests[1]['response_format'], {'type': 'json_object'})

    def test_invalid_output_is_not_accepted_after_fallback(self):
        skill = load_skill('precision_intervention_activity_formative_regulation')
        source = json.loads((skill.path.parent / 'tests/sample-input.json').read_text(encoding='utf-8'))
        invalid = {'choices': [{'message': {'content': '{}'}}]}
        with patch.object(ai, 'settings', replace(ai.settings, ai_api_key='test', ai_model='test')), patch.object(ai, '_post_json', side_effect=[RuntimeError('模型请求失败（400）：The compiled grammar is too large'), invalid, invalid]):
            with self.assertRaisesRegex(RuntimeError, '学习活动输出校验'):
                ai.run_activity_formative_design(skill, source, 'openrouter')
