import copy
import json
import unittest
from unittest.mock import patch

from backend.app.services import ai
from backend.app.services.skills import load_skill


class RubricFormatRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.skill = load_skill('solo_task_rubric_builder')
        self.schema = json.loads((self.skill.path.parent/'assets/task-specific-solo-rubric.schema.json').read_text())
        self.rubric = ai._mock_task_rubric({'precision_teaching_context': {'precision_teaching_topic': '解释理由'},
                                         'diagnostic_task': '比较两个方案并说明理由。'})
        self.calls = []

    def response(self, value):
        return {'choices': [{'message': {'content': json.dumps(value, ensure_ascii=False)}}]}

    def run_outputs(self, outputs):
        items = iter(outputs)
        def post(url, body, key, **kwargs):
            self.calls.append((copy.deepcopy(body), kwargs))
            value = next(items)
            if isinstance(value, Exception): raise value
            return self.response(value)
        with patch.object(ai, '_post_json', side_effect=post):
            return ai._run_rubric_compatible(self.skill, {}, self.schema, 'rubric', '')

    def test_shape_is_smaller_but_keeps_fields_and_full_validation(self):
        simple = ai._rubric_transport_schema(self.schema)
        self.assertLess(len(json.dumps(simple)),len(json.dumps(self.schema)))
        self.assertEqual(simple['required'],self.schema['required'])
        self.assertNotIn('maxItems',json.dumps(simple))
        self.assertEqual(self.run_outputs([self.rubric]),self.rubric)
        self.assertIn(json.dumps(self.schema,ensure_ascii=False),self.calls[0][0]['messages'][0]['content'])

    def test_grammar_error_falls_back_to_json(self):
        error = RuntimeError('模型请求失败（400）：The compiled grammar is too large')
        self.assertEqual(self.run_outputs([error,self.rubric]),self.rubric)
        self.assertEqual(self.calls[1][0]['response_format'],{'type':'json_object'})

    def test_unsupported_json_mode_uses_prompt_only(self):
        self.run_outputs([RuntimeError('模型请求失败（400）：compiled grammar is too large'),
                          RuntimeError('模型请求失败（400）：response_format json_object is not supported'),self.rubric])
        self.assertNotIn('response_format',self.calls[2][0])

    def test_auth_rate_limit_balance_and_other_bad_requests_do_not_retry(self):
        for message in ['模型请求失败（401）：bad key','模型请求失败（429）：rate limit',
                        '模型请求失败（402）：balance','模型请求失败（400）：invalid model']:
            with self.subTest(message=message):
                self.calls=[]
                with self.assertRaises(RuntimeError):self.run_outputs([RuntimeError(message)])
                self.assertEqual(len(self.calls),1)

    def test_invalid_output_and_incomplete_levels_get_one_repair(self):
        for bad in [{},copy.deepcopy(self.rubric)]:
            if bad:bad['levels']['EA']['typical_expressions']=[]
            self.calls=[]
            self.assertEqual(self.run_outputs([bad,self.rubric]),self.rubric)
            self.assertEqual(len(self.calls),2)
            self.assertIn('校验问题',self.calls[1][0]['messages'][-1]['content'])

    def test_failed_repair_never_returns_invalid_draft(self):
        with self.assertRaisesRegex(RuntimeError,'原量规已保留'):self.run_outputs([{},{}])
        self.assertEqual(len(self.calls),2)

    def test_fallback_and_repair_have_four_request_limit(self):
        self.run_outputs([RuntimeError('模型请求失败（400）：compiled grammar is too large'),
                          RuntimeError('模型请求失败（400）：response_format unsupported'),{},self.rubric])
        self.assertEqual(len(self.calls),4)
        self.assertTrue(all(0<kwargs['timeout']<=120 for _,kwargs in self.calls))

    def test_total_deadline_prevents_another_request(self):
        with patch.object(ai.time,'monotonic',side_effect=[0,181]),patch.object(ai,'_post_json') as post:
            with self.assertRaisesRegex(RuntimeError,'超时'):
                ai._run_rubric_compatible(self.skill,{},self.schema,'rubric','')
            post.assert_not_called()

    def test_failed_generation_preserves_existing_saved_rubric(self):
        from fastapi import HTTPException
        from backend.app import database as db, main
        from backend.test_task_version_runtime import TaskVersionRuntimeTest
        fixture = TaskVersionRuntimeTest('test_unchanged_publish_does_not_create_spurious_versions')
        fixture.setUp()
        try:
            before = db.fetch_one("SELECT * FROM diagnosis_rubrics WHERE teaching_id='pt'")
            with patch.object(main,'run_skill',side_effect=RuntimeError('量规修正失败')):
                with self.assertRaises(HTTPException):main.generate_diagnosis_rubric('pt')
            self.assertEqual(db.fetch_one("SELECT * FROM diagnosis_rubrics WHERE teaching_id='pt'"),before)
        finally:
            fixture.doCleanups()
