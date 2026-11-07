"""Class analysis requests one JSON result and renders teacher text locally."""
import copy
import json
from dataclasses import replace
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from jsonschema import ValidationError
from backend.app.services import ai
from backend.app.services.compact_outputs import class_transport_schema
def compact(value):
    result=copy.deepcopy(value)
    result.pop("class_summary",None);result.pop("traceability",None)
    for row in result["level_analyses"]:
        row.pop("students",None);row.pop("count",None)
    return result
from backend.app.services.class_report import render_class_markdown, validate_class_result
from backend.app.services.skills import load_skill

class ClassReportJsonOnlyTest(TestCase):
    def setUp(self):
        self.skill = load_skill("solo_class_diagnosis_intervention")
        self.assets = self.skill.path.parent
        self.payload = json.loads((self.assets/"tests/sample-input.json").read_text(encoding="utf-8"))
        self.result = json.loads((self.assets/"tests/sample-output.json").read_text(encoding="utf-8"))

    def test_actual_request_uses_simplified_schema_and_full_prompt_rules(self):
        response={"choices":[{"message":{"content":json.dumps(compact(self.result))}}]}
        with patch.object(ai,"settings",replace(ai.settings,ai_api_key="isolated-test-key",ai_model="test-model")), patch.object(ai,"_post_json",return_value=response) as post:
            result,text = ai.run_class_diagnosis_report(self.skill,self.payload,"openrouter")
        self.assertEqual(result["overall_diagnosis"],self.result["overall_diagnosis"])
        self.assertIsNone(text)
        self.assertEqual(post.call_count,1)
        body=post.call_args.args[1]
        schema=body["response_format"]["json_schema"]["schema"]
        expected=json.loads((self.assets/"assets/class-diagnosis-output.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema,ai._rubric_transport_schema(class_transport_schema(expected)))
        self.assertIn(json.dumps(class_transport_schema(expected),ensure_ascii=False),body['messages'][0]['content'])
        self.assertNotIn("teacher_report_markdown",schema["properties"])
        self.assertNotIn("solo_class_diagnosis",schema["properties"])
        self.assertNotIn("输出教师可读 Markdown，再输出",body["messages"][0]["content"])
        validate_class_result(result,self.payload)
        report=render_class_markdown(result)
        for title in ("班级 SOLO 分布","整体","教学重点","分组建议"):
            self.assertIn(title,report)

    def test_incomplete_structured_analysis_remains_rejected(self):
        invalid=copy.deepcopy(self.result)
        del invalid["overall_diagnosis"]
        with patch.object(ai,"_run_structured_skill",return_value=invalid), self.assertRaises(ValidationError):
            ai.run_class_diagnosis_report(self.skill,self.payload,"openrouter")

    def test_mock_still_cannot_create_formal_report(self):
        with self.assertRaisesRegex(RuntimeError,"真实模型"):
            ai.run_class_diagnosis_report(self.skill,self.payload,"mock")
