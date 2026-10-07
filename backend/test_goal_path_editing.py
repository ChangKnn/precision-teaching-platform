"""Teacher edits to progression goals preserve the diagnosis roster."""

import copy
import json
from pathlib import Path
from unittest import TestCase
from jsonschema import ValidationError

from backend.app.goal_path_api import _validate_design


SKILL = Path(__file__).parent / "skills/precision-intervention-goal-path-design/tests"


class GoalPathEditingTests(TestCase):
    def setUp(self):
        sample_input = json.loads((SKILL / "sample-input.json").read_text(encoding="utf-8"))
        self.class_result = sample_input["diagnosis_bundle"]["class_diagnosis"]
        self.result = json.loads((SKILL / "sample-output.json").read_text(encoding="utf-8"))

    def test_teacher_can_rename_level_and_reassign_student(self):
        edited = copy.deepcopy(self.result)
        first, second, third = edited["progression_goals"]
        first["target_level_name"] = "建立有效起点"
        moved = second["target_students"].pop()
        third["target_students"].append(moved)
        third["source_levels"] = ["M", "R"]
        next(item for item in edited["student_goal_assignments"] if item["student_id"] == moved["student_id"])["primary_goal_id"] = third["goal_id"]
        _validate_design(edited, self.class_result, 45)

    def test_rejects_student_list_out_of_sync_with_source_levels(self):
        edited = copy.deepcopy(self.result)
        edited["progression_goals"][0]["source_levels"] = ["M"]
        with self.assertRaisesRegex(ValueError, "面向层级"):
            _validate_design(edited, self.class_result, 45)

    def test_common_core_goal_can_be_targeted_without_all_progression_goals(self):
        first_unit = self.result["intervention_path"]["stages"][0]["activity_units"][0]
        self.assertEqual(first_unit["target_goal_ids"], ["CG"])
        _validate_design(self.result, self.class_result, 45)

    def test_rejects_multiple_goal_levels_for_one_activity(self):
        edited = copy.deepcopy(self.result)
        edited["intervention_path"]["stages"][0]["activity_units"][0]["target_goal_ids"] = ["PG1", "PG2"]
        with self.assertRaises(ValidationError):
            _validate_design(edited, self.class_result, 45)

    def test_teacher_can_add_a_progression_goal(self):
        edited = copy.deepcopy(self.result)
        original_goal = edited["progression_goals"][1]
        moved = original_goal["target_students"].pop()
        edited["progression_goals"].append({
            "goal_id": "PG4", "target_level_code": "CUSTOM",
            "target_level_name": "补充证据解释", "source_levels": [moved["current_level"]],
            "target_students": [moved], "goal_statement": "用两条证据解释实验结论。",
            "observable_achievement": "能指出两条证据并说明各自作用。",
            "custom_goal_note": "补充证据解释",
        })
        next(item for item in edited["student_goal_assignments"] if item["student_id"] == moved["student_id"])["primary_goal_id"] = "PG4"
        _validate_design(edited, self.class_result, 45)

    def test_teacher_can_merge_and_delete_a_progression_goal(self):
        edited = copy.deepcopy(self.result)
        removed = edited["progression_goals"].pop(0)
        destination = edited["progression_goals"][0]
        destination["target_students"].extend(removed["target_students"])
        destination["source_levels"] = sorted(set(destination["source_levels"] + removed["source_levels"]))
        next(item for item in edited["student_goal_assignments"] if item["student_id"] == removed["target_students"][0]["student_id"])["primary_goal_id"] = destination["goal_id"]
        unit = edited["intervention_path"]["stages"][1]["activity_units"][0]
        unit["target_goal_ids"] = [destination["goal_id"]]
        _validate_design(edited, self.class_result, 45)
