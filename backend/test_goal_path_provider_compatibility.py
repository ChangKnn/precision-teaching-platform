import copy
import json
import unittest
from dataclasses import replace
from unittest.mock import patch
from backend.app.services import ai
from backend.app.services.skills import load_skill

class GoalPathProviderCompatibilityTest(unittest.TestCase):
    def test_unsupported_schema_falls_back_and_validates_full_output(self):
        skill=load_skill('precision_intervention_goal_path_design')
        sample=json.loads((skill.path.parent/'tests/sample-output.json').read_text(encoding='utf-8'))
        calls=[]
        def post(url,body,key,**kwargs):
            calls.append(copy.deepcopy(body))
            if len(calls)==1:raise RuntimeError("模型请求失败（400）：output_config.format.schema: Schema type 'oneOf' is not supported")
            return {'choices':[{'message':{'content':json.dumps(sample,ensure_ascii=False)}}]}
        with patch.object(ai,'settings',replace(ai.settings,ai_api_key='test-key',ai_model='test-model')),patch.object(ai,'_post_json',side_effect=post):
            result,_=ai.run_goal_path_design(skill,{},'openrouter')
        self.assertEqual(result,sample)
        self.assertEqual(calls[1]['response_format'],{'type':'json_object'})
        self.assertNotIn('oneOf',json.dumps(calls[0]['response_format']))

    def test_auth_errors_do_not_fallback(self):
        self.assertFalse(ai._rubric_format_rejected(RuntimeError("模型请求失败（401）：Schema type 'oneOf' is not supported")))

    def test_revision_fallback_keeps_teacher_changes_as_structured_operations(self):
        from backend.app.services.goal_path_revision import REVISION_SCHEMA
        skill=load_skill('precision_intervention_goal_path_design')
        calls=[]
        def post(url,body,key,**kwargs):
            calls.append(copy.deepcopy(body))
            if len(calls)==1:raise RuntimeError('模型请求失败（400）：output_config.format.schema: Empty schema ({}) not supported')
            return {'choices':[{'message':{'content':'{"changes":[]}'}}]}
        with patch.object(ai,'settings',replace(ai.settings,ai_api_key='test-key',ai_model='test-model')),patch.object(ai,'_post_json',side_effect=post):
            result=ai._run_structured_skill(skill,{},'goal-path-output.schema.json','precision_intervention_goal_path_revision','openrouter',schema_override=REVISION_SCHEMA)
        self.assertEqual(result,{'changes':[]})
        self.assertEqual(calls[1]['response_format'],{'type':'json_object'})
