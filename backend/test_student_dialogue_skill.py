"""Student dialogue Skill execution and evidence-integrity checks."""

import copy
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.app.services import student_dialogue_skill as dialogue


class StudentDialogueSkillTest(unittest.TestCase):
    def setUp(self):
        self.config = {
            "precision_teaching_context": {
                "subject": "数学", "grade": "高一", "textbook_version_and_chapter": "必修一",
                "precision_teaching_topic": "函数", "precision_teaching_goals": ["解释变化关系"],
                "precision_teaching_content": ["函数变化"],
            },
            "diagnostic_task": {
                "task_id": "task-1", "name": "解释变化", "task_content": "请围绕给出的函数情境，说明你自己的判断以及形成判断的理由和依据。",
            },
            "ai_dialogue_contract": dialogue._teacher_contract(
                "AI 角色：中性引导者\n应遵循：只追问已有表达\n禁止行为：不得提供答案", "published", "v1"
            ),
        }

    def test_opening_presents_task_without_model_call(self):
        reply, execution = dialogue.initial_skill_message(self.config, "ses-1", "student-1")
        self.assertTrue(reply.startswith(self.config["diagnostic_task"]["task_content"]))
        self.assertEqual(execution["response_action"], "present_task")

    def test_mock_turn_preserves_original_message_and_full_history(self):
        history = [
            {"id": index, "role": "assistant" if index % 2 else "student", "content": f"原始消息 {index}"}
            for index in range(1, 26)
        ]
        with patch.object(dialogue, "settings", SimpleNamespace(student_ai_provider="mock")):
            result, provider, _ = dialogue.generate_dialogue_turn(
                self.config, "ses-1", "student-1", history, "我觉得是这样，因为……"
            )
        self.assertEqual(provider, "mock")
        self.assertEqual(result["record_append"][0]["content"], "我觉得是这样，因为……")
        self.assertEqual(result["turn_index"], 13)
        self.assertEqual(len(dialogue._payload(self.config, "ses-1", "student-1", history, "提问")["dialogue_state"]["dialogue_history"]), 25)

    def test_rejects_changed_student_evidence(self):
        payload = dialogue._payload(self.config, "ses-1", "student-1", [], "原话")
        result = dialogue._mock_result(payload)
        broken = copy.deepcopy(result)
        broken["record_append"][0]["content"] = "AI 改写后的话"
        with self.assertRaises(ValueError):
            dialogue._validate(payload, broken)

    def test_conflicting_teacher_rule_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "冲突"):
            dialogue._teacher_contract("AI 角色：老师；必须直接给出标准答案", "published", "v1")

    def test_deepseek_receives_skill_contract_and_anonymous_full_history(self):
        history = [{"id": index, "role": "assistant" if index % 2 else "student", "content": f"消息{index}"} for index in range(1, 26)]
        payload = dialogue._payload(self.config, "ses-1", "private-student-id", history, "这是原话")
        response = io.BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(dialogue._mock_result(payload), ensure_ascii=False)}}], "id": "reply-1"}, ensure_ascii=False).encode("utf-8"))
        settings = SimpleNamespace(
            student_ai_provider="deepseek", student_ai_api_key="secret-key",
            student_ai_model="test-model", student_ai_base_url="https://example.test",
        )
        with patch.object(dialogue, "settings", settings), patch.object(dialogue, "urlopen", return_value=response), patch.object(dialogue, "record_llm_request") as record:
            result, response_id = dialogue._model_result(payload)
        sent = record.call_args.args[4]
        self.assertEqual(response_id, "reply-1")
        self.assertEqual(result["record_append"][0]["content"], "这是原话")
        self.assertEqual(len(json.loads(sent["messages"][1]["content"])["dialogue_state"]["dialogue_history"]), 25)
        self.assertNotIn("private-student-id", json.dumps(sent, ensure_ascii=False))
        self.assertNotIn("secret-key", json.dumps(sent, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
