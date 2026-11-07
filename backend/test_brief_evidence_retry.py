import copy
import json
import unittest
from unittest.mock import patch
from backend.app.services import ai
from backend.app.services.skills import load_skill

class BriefEvidenceRetryTest(unittest.TestCase):
    def test_invented_quote_is_repaired_without_accepting_ai_text(self):
        skill=load_skill('solo_student_diagnosis_feedback')
        schema=json.loads((skill.path.parent/'assets/diagnosis-output.schema.json').read_text())
        valid=json.loads((skill.path.parent/'tests/sample-output.json').read_text())
        quote='学生说出的原句'
        valid['diagnosis']['evidence']=[{'source':'dialogue','ref':'turn-1','quote':quote,'interpretation':'体现学生的实际依据','evidence_role':'autonomous_content'}]
        invalid=copy.deepcopy(valid);invalid['diagnosis']['evidence'][0]['quote']='AI提供的内容'
        payload={'student':valid['student'],'human_ai_dialogue':[{'role':'student','content':quote},{'role':'ai','content':'AI提供的内容'}],'submitted_text':''}
        def response(value):return {'choices':[{'message':{'content':json.dumps(value,ensure_ascii=False)}}]}
        with patch.object(ai,'_post_json',side_effect=[response(invalid),response(valid)]) as post:
            result=ai._run_rubric_compatible(skill,payload,schema,'student_brief','')
        self.assertEqual(result,valid);self.assertEqual(post.call_count,2)
        self.assertIn('逐字复制',post.call_args_list[1].args[1]['messages'][-1]['content'])
