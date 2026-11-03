"""Free-text activity edits remain authoritative in reports and integration."""

import json
import unittest
from pathlib import Path

from backend.app.activity_formative_api import _markdown
from backend.app.intervention_integration_api import _apply_teacher_overrides


SAMPLE = Path(__file__).parent / "skills/precision-intervention-activity-formative-regulation/tests/sample-output.json"


class ActivityPresentationOverridesTest(unittest.TestCase):
    def test_explicit_empty_draft_does_not_fall_back_to_generated_text(self):
        result = json.loads(SAMPLE.read_text(encoding="utf-8"))
        activity = result["activities"][0]
        overrides = {activity["activity_id"]: {"objective_full": "", "student_full": "", "product_full": ""}}
        report = _markdown(result, overrides)
        first = report.split("### 活动2")[0]
        self.assertIn("| 活动目标 |  |", first)
        self.assertIn("| 学习产出 |  |", first)
        merged = _apply_teacher_overrides(result, overrides)
        self.assertEqual(merged["activities"][0]["activity_objective"], "")
        self.assertEqual(merged["activities"][0]["student_task"], "")
        self.assertTrue(result["activities"][0]["activity_objective"])

    def test_new_student_text_is_added_when_model_had_no_student_action(self):
        result = json.loads(SAMPLE.read_text(encoding="utf-8"))
        activity = result["activities"][0]
        activity["participant_actions"] = [item for item in activity["participant_actions"] if item["actor"] not in {"学生", "同伴"}]
        merged = _apply_teacher_overrides(result, {activity["activity_id"]: {"student_full": "独立写出理由，再比较不同解释。"}})
        actions = merged["activities"][0]["participant_actions"]
        self.assertIn("独立写出理由，再比较不同解释。", [item["action"] for item in actions if item["actor"] == "学生"])
        self.assertEqual([item["order"] for item in actions], list(range(1, len(actions) + 1)))

    def test_full_text_keeps_edited_group_names_and_manual_numbering(self):
        result = json.loads(SAMPLE.read_text(encoding="utf-8"))
        activity = result["activities"][1]
        activity_id = activity["activity_id"]
        full_student = "1. 先看题目\n\n新组名\n2. 标出关键关系"
        full_product = "新组名\n一张关系图"
        overrides = {activity_id: {"student_full": full_student, "product_full": full_product}}

        report = _markdown(result, overrides)
        self.assertIn("1. 先看题目<br><br>新组名<br>2. 标出关键关系", report)
        self.assertIn("新组名<br>一张关系图", report)

        merged = _apply_teacher_overrides(result, overrides)
        edited = merged["activities"][1]
        self.assertEqual(edited["learning_product"], full_product)
        self.assertIn(full_student, [item["action"] for item in edited["participant_actions"]])


if __name__ == "__main__":
    unittest.main()
