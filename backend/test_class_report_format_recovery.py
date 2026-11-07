import copy
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from backend.app.services import ai
from backend.app.services.skills import load_skill


class ClassReportFormatRecoveryTest(unittest.TestCase):
    def test_incomplete_json_retries_without_feeding_a_cut_off_assistant_reply(self):
        skill=load_skill('solo_class_diagnosis_intervention')
        sample=json.loads((skill.path.parent/'tests/sample-output.json').read_text(encoding='utf-8'))
        responses=[{'choices':[{'message':{'content':'{"class_summary":'},'finish_reason':'length'}]},
                   {'choices':[{'message':{'content':json.dumps(sample,ensure_ascii=False)}}]}]
        with patch.object(ai,'settings',replace(ai.settings,ai_api_key='test-key',ai_model='test-model')), patch.object(ai,'_post_json',side_effect=responses) as post:
            result,_=ai.run_class_diagnosis_report(skill,{},'openrouter')
        self.assertEqual(result,sample)
        messages=post.call_args_list[1].args[1]['messages']
        self.assertFalse(any(message['role']=='assistant' for message in messages))
        self.assertIn('重新生成一个完整JSON',messages[-1]['content'])

    def test_failed_repair_reports_only_schema_location_not_student_text(self):
        skill = load_skill('solo_class_diagnosis_intervention')
        secret = 'PRIVATE_STUDENT_CONTENT'
        response = {'choices':[{'message':{'content':json.dumps({'student':secret})}}]}
        with patch.object(ai,'settings',replace(ai.settings,ai_api_key='test-key',ai_model='test-model')), patch.object(ai,'_post_json',return_value=response):
            with self.assertRaises(RuntimeError) as error:
                ai.run_class_diagnosis_report(skill,{},'openrouter')
        self.assertIn('格式规则',str(error.exception))
        self.assertNotIn(secret,str(error.exception))

    def test_transport_has_concrete_type_for_boolean_constant(self):
        skill = load_skill('solo_class_diagnosis_intervention')
        schema = json.loads((skill.path.parent/'assets/class-diagnosis-output.schema.json').read_text(encoding='utf-8'))
        transport = ai._rubric_transport_schema(schema)
        self.assertEqual(transport['properties']['traceability']['properties']['levels_unchanged'], {'type':'boolean'})

    def test_empty_schema_rejection_can_fallback(self):
        self.assertTrue(ai._rubric_format_rejected(RuntimeError('模型请求失败（400）：Invalid schema: Empty schema ({}) that accepts any JSON value is not supported')))
        self.assertFalse(ai._rubric_format_rejected(RuntimeError('模型请求失败（401）：Empty schema not supported')))

    def test_grammar_error_retries_json_and_validates_full_schema(self):
        skill = load_skill('solo_class_diagnosis_intervention')
        sample = json.loads((skill.path.parent/'tests/sample-output.json').read_text(encoding='utf-8'))
        calls = []
        def post(url, body, key, **kwargs):
            calls.append(copy.deepcopy(body))
            if len(calls) == 1:
                raise RuntimeError('模型请求失败（400）：The compiled grammar is too large')
            return {'choices':[{'message':{'content':json.dumps(sample,ensure_ascii=False)}}]}
        with patch.object(ai,'settings',replace(ai.settings,ai_api_key='test-key',ai_model='test-model')), patch.object(ai,'_post_json',side_effect=post):
            result, text = ai.run_class_diagnosis_report(skill,{},'openrouter')
        self.assertEqual(result,sample)
        self.assertIsNone(text)
        self.assertEqual(calls[1]['response_format'],{'type':'json_object'})

    def test_auth_error_is_not_retried(self):
        skill = load_skill('solo_class_diagnosis_intervention')
        with patch.object(ai,'settings',replace(ai.settings,ai_api_key='test-key',ai_model='test-model')), patch.object(ai,'_post_json',side_effect=RuntimeError('模型请求失败（401）：bad key')) as post:
            with self.assertRaisesRegex(RuntimeError,'401'):
                ai.run_class_diagnosis_report(skill,{},'openrouter')
            self.assertEqual(post.call_count,1)
