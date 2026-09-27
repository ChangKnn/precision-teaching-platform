"""Regression tests for extra content in DeepSeek structured output."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.app.services import ai


SCHEMA = {
    "type": "object", "required": ["plan"], "additionalProperties": False,
    "properties": {"plan": {"type": "string", "minLength": 1}},
}


class StructuredJsonTest(unittest.TestCase):
    def test_uses_first_complete_schema_valid_object_with_extra_data(self):
        result = ai._parse_structured_json('{"plan":"完整方案"}\n{"plan":"重复内容"}', SCHEMA)
        self.assertEqual(result, {"plan": "完整方案"})

    def test_uses_adjacent_corrected_object_when_first_is_invalid(self):
        result = ai._parse_structured_json('{"wrong":1}\n{"plan":"修正方案"}', SCHEMA)
        self.assertEqual(result, {"plan": "修正方案"})

    def test_rejects_incomplete_or_schema_invalid_object(self):
        for text in ('{"plan":', '{"wrong":1}', '这不是 JSON'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                ai._parse_structured_json(text, SCHEMA)

    def test_retries_invalid_schema_and_returns_second_response(self):
        responses = [
            {"choices": [{"message": {"content": '{"wrong":1}'}}]},
            {"choices": [{"message": {"content": '{"plan":"可用方案"}'}}]},
        ]
        settings = SimpleNamespace(ai_model="test-model", ai_base_url="https://example.test", ai_api_key="test-key")
        with patch.object(ai, "settings", settings), patch.object(ai, "_skill_runtime_instructions", return_value="instructions"), patch.object(ai, "_post_json", side_effect=responses) as post:
            result = ai._run_deepseek_structured_skill(SimpleNamespace(key="test_skill"), {}, SCHEMA, "plan")
        self.assertEqual(result, {"plan": "可用方案"})
        self.assertEqual(post.call_count, 2)


if __name__ == "__main__":
    unittest.main()
