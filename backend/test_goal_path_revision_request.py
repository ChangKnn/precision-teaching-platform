"""The goal/path skill accepts a teacher's revision request with the prior plan."""

import json
from pathlib import Path
from unittest import TestCase

from jsonschema import validate

from backend.app.goal_path_api import GoalPathGenerateRequest


SKILL = Path(__file__).parent / "skills/precision-intervention-goal-path-design"


class GoalPathRevisionRequestTests(TestCase):
    def test_revision_input_keeps_request_separate_from_teaching_conditions(self):
        sample = json.loads((SKILL / "tests/sample-input.json").read_text(encoding="utf-8"))
        previous = json.loads((SKILL / "tests/sample-output.json").read_text(encoding="utf-8"))
        sample["teacher_revision_request"] = "保留目标，改进分组活动名称"
        sample["previous_goal_path_design"] = previous
        schema = json.loads((SKILL / "assets/goal-path-input.schema.json").read_text(encoding="utf-8"))
        validate(sample, schema)
        request = GoalPathGenerateRequest(
            teacher_instructional_context=sample["teacher_instructional_context"],
            regeneration_request=sample["teacher_revision_request"],
        )
        self.assertEqual(request.regeneration_request, sample["teacher_revision_request"])
        self.assertNotIn("regeneration_request", request.teacher_instructional_context.model_dump())
