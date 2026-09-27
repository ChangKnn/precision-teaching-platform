"""The development request viewer receives exactly the outbound JSON body."""

import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.app.services import ai, student_chat


class LLMRequestDebugTest(unittest.TestCase):
    def test_teacher_request_is_captured_before_send(self):
        body = {"model": "test-model", "messages": [{"role": "user", "content": "测试"}]}
        fake_response = io.BytesIO(b'{"choices":[]}')
        with patch.object(ai, "record_llm_request") as record, patch.object(ai, "urlopen", return_value=fake_response):
            ai._post_json("https://example.test/chat/completions", body, "secret-key", purpose="test_skill")
        self.assertEqual(record.call_args.args[4], body)
        self.assertNotIn("secret-key", json.dumps(record.call_args.args, ensure_ascii=False))

    def test_student_request_contains_actual_system_prompt_and_history(self):
        settings = SimpleNamespace(
            student_ai_api_key="secret-key", student_ai_model="test-model",
            student_ai_base_url="https://example.test", student_ai_provider="deepseek",
        )
        response = io.BytesIO(json.dumps({"choices": [{"message": {"content": "请再说明你的理由。"}}], "id": "reply-1"}, ensure_ascii=False).encode("utf-8"))
        with patch.object(student_chat, "settings", settings), patch.object(student_chat, "record_llm_request") as record, patch.object(student_chat, "urlopen", return_value=response):
            student_chat._deepseek_reply(
                {"ai_role": "只追问理由", "task_text": "请解释现象"},
                [{"role": "student", "content": "我认为会继续运动"}], "student-1",
            )
        captured = record.call_args.args[4]
        self.assertIn("只追问理由", captured["messages"][0]["content"])
        self.assertEqual(captured["messages"][1]["content"], "我认为会继续运动")
        self.assertNotIn("secret-key", json.dumps(captured, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
