"""Regression test for a model that writes parallel groups as sequential activities."""

import copy
import json
import unittest
from pathlib import Path

from backend.app.activity_formative_api import _normalize_common_goal_mapping, _normalize_parallel_activities, _validate


SAMPLES = Path(__file__).parent / "skills/precision-intervention-activity-formative-regulation/tests"


class ActivityFormativeNormalizationTest(unittest.TestCase):
    def test_common_goal_is_kept_for_evaluation_but_not_unit_mapping(self):
        source = json.loads((SAMPLES / "sample-input.json").read_text(encoding="utf-8"))
        result = json.loads((SAMPLES / "sample-output.json").read_text(encoding="utf-8"))
        goal = source["goal_path_design"]
        goal["intervention_path"] = goal["intervention_path"]["primary"]
        for stage, summary in zip(goal["intervention_path"]["stages"], result["activity_sequence_summary"]["stages"]):
            stage["stage_name"] = summary["stage_name"]
            for unit in stage["activity_units"]:
                unit["target_students"] = [{"student_id": student_id} for student_id in unit["target_student_ids"]]
        activity = result["activities"][-1]
        activity["target_goal_ids"].append("CG")
        evaluation = result["formative_evaluation_nodes"][-1]
        evaluation["target_goal_ids"].append("CG")
        criterion = copy.deepcopy(evaluation["judgment_criteria"][0])
        criterion["goal_id"] = "CG"
        evaluation["judgment_criteria"].append(criterion)

        self.assertEqual(_normalize_common_goal_mapping(result, goal), [activity["activity_id"]])
        self.assertNotIn("CG", activity["target_goal_ids"])
        self.assertIn("CG", evaluation["target_goal_ids"])
        _validate(result, goal)

    def test_single_parallel_activity_uses_upstream_simultaneity(self):
        source = json.loads((SAMPLES / "sample-input.json").read_text(encoding="utf-8"))
        result = json.loads((SAMPLES / "sample-output.json").read_text(encoding="utf-8"))
        goal = source["goal_path_design"]
        goal["intervention_path"] = goal["intervention_path"]["primary"]
        result["activity_sequence_summary"]["stages"][1]["simultaneous"] = False
        self.assertEqual(_normalize_parallel_activities(result, goal), [])
        self.assertTrue(result["activity_sequence_summary"]["stages"][1]["simultaneous"])

    def test_parallel_stage_is_grouped_without_changing_upstream_units(self):
        source = json.loads((SAMPLES / "sample-input.json").read_text(encoding="utf-8"))
        result = json.loads((SAMPLES / "sample-output.json").read_text(encoding="utf-8"))
        goal = copy.deepcopy(source["goal_path_design"])
        goal["intervention_path"] = goal["intervention_path"]["primary"]
        for stage, summary in zip(goal["intervention_path"]["stages"], result["activity_sequence_summary"]["stages"]):
            stage["stage_name"] = summary["stage_name"]
            for unit in stage["activity_units"]:
                unit["target_students"] = [{"student_id": student_id} for student_id in unit["target_student_ids"]]

        original = result["activities"][1]
        split = []
        for index, group in enumerate(original["parallel_group_tasks"], 1):
            item = copy.deepcopy(original)
            item.update(
                activity_id=f"ACT-ST2-{index}", source_unit_ids=[group["source_unit_id"]],
                sequence_within_stage=index, activity_name=group["group_name"],
                activity_objective=group["activity_objective"],
                target_goal_ids=group["target_goal_ids"], target_student_ids=group["target_student_ids"],
                organization_forms=[group["organization_form"]], dialogue_circles=group["dialogue_circles"],
                duration_minutes=[7, 7, 6][index - 1], learning_materials=group["learning_materials"],
                student_task=group["student_task"], cognitive_processing=group["cognitive_processing"],
                scaffold_support=group["scaffold_support"], learning_product=group["learning_product"],
                parallel_group_tasks=[],
            )
            split.append(item)
        result["activities"][1:2] = split
        stage_summary = result["activity_sequence_summary"]["stages"][1]
        stage_summary["activity_ids"] = [item["activity_id"] for item in split]
        stage_summary["simultaneous"] = False

        self.assertEqual(_normalize_parallel_activities(result, goal), ["ST2"])
        _validate(result, goal)
        self.assertEqual(stage_summary["activity_ids"], ["ACT-ST2-1"])
        self.assertEqual(len(result["activities"][1]["parallel_group_tasks"]), 3)
        self.assertEqual(result["activities"][1]["duration_minutes"], 20)


if __name__ == "__main__":
    unittest.main()
