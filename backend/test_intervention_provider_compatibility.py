import copy
import json
from dataclasses import replace
from unittest import TestCase
from unittest.mock import patch

from backend.app.services import ai
from backend.app.services.skills import load_skill


class InterventionProviderCompatibilityTest(TestCase):
    def test_long_plan_restarts_after_truncation_without_reusing_partial_json(self):
        skill = load_skill('precision_intervention_plan_integration_review')
        source = json.loads((skill.path.parent / 'tests/sample-input.json').read_text(encoding='utf-8'))
        output = json.loads((skill.path.parent / 'tests/sample-output.json').read_text(encoding='utf-8'))
        requests = []
        def post(url, body, api_key, **kwargs):
            requests.append(copy.deepcopy(body))
            if len(requests) == 1:
                return {'choices': [{'finish_reason': 'length', 'message': {'content': '{"partial":"TRUNCATED_SENTINEL'}}]}
            return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(output)}}]}
        with patch.object(ai, 'settings', replace(ai.settings, ai_api_key='test', ai_model='anthropic/claude-haiku-5.5')), patch.object(ai, '_post_json', side_effect=post):
            self.assertEqual(ai.run_intervention_plan_integration(skill, source, 'openrouter'), output)
        self.assertEqual(requests[0]['max_tokens'], 32000)
        self.assertEqual(requests[1]['max_tokens'], 32000)
        self.assertNotIn('TRUNCATED_SENTINEL', json.dumps(requests[1]))
        self.assertIn('JSON 不完整', requests[1]['messages'][-1]['content'])

    def test_audit_fills_only_known_labels_and_pass_placeholders(self):
        skill = load_skill('precision_intervention_plan_integration_review')
        sample = json.loads((skill.path.parent / 'tests/sample-output.json').read_text(encoding='utf-8'))
        schema = json.loads((skill.path.parent / 'assets/plan-integration-output.schema.json').read_text(encoding='utf-8'))
        row = sample['audit_summary']['review_items'][0]
        row.update(status='✓ 通过')
        for field in ('check_item', 'issue', 'suggestion', 'return_to'):
            row.pop(field)
        normalized = ai._parse_structured_json(json.dumps(sample), schema, normalize=ai._normalize_integration_audit)
        fixed = normalized['audit_summary']['review_items'][0]
        self.assertEqual(fixed['check_item'], '诊断—目标一致性')
        self.assertEqual((fixed['issue'], fixed['suggestion'], fixed['return_to']), ('—', '—', '—'))
        row['status'] = '× 需要修改'
        with self.assertRaises(ai.StructuredOutputError):
            ai._parse_structured_json(json.dumps(sample), schema, normalize=ai._normalize_integration_audit)

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
