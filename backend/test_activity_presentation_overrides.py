"""Free-text activity edits remain authoritative in reports and integration."""

import json
import unittest
from pathlib import Path

from backend.app.activity_formative_api import _markdown
from backend.app.intervention_integration_api import _apply_teacher_overrides


SAMPLE = Path(__file__).parent / "skills/precision-intervention-activity-formative-regulation/tests/sample-output.json"


class ActivityPresentationOverridesTest(unittest.TestCase):
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
