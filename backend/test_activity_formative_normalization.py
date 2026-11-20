"""Regression test for a model that writes parallel groups as sequential activities."""

import copy
import json
import unittest
from pathlib import Path

from backend.app.activity_formative_api import _normalize_common_goal_mapping, _normalize_parallel_activities, _validate


SAMPLES = Path(__file__).parent / "skills/precision-intervention-activity-formative-regulation/tests"


class ActivityFormativeNormalizationTest(unittest.TestCase):
    def test_common_core_goal_is_valid_as_source_activity_target(self):
        source = json.loads((SAMPLES / "sample-input.json").read_text(encoding="utf-8"))
        result = json.loads((SAMPLES / "sample-output.json").read_text(encoding="utf-8"))
        goal = source["goal_path_design"]
        goal["intervention_path"] = goal["intervention_path"]["primary"]
        for stage, summary in zip(goal["intervention_path"]["stages"], result["activity_sequence_summary"]["stages"]):
            stage["stage_name"] = summary["stage_name"]
        goal["intervention_path"]["stages"][0]["activity_units"][0]["target_goal_ids"] = ["CG"]
        result["activities"][0]["target_goal_ids"] = ["CG"]
        _validate(result, goal)

    def test_common_goal_is_kept_for_evaluation_but_not_unit_mapping(self):
        source = json.loads((SAMPLES / "sample-input.json").read_text(encoding="utf-8"))
        result = json.loads((SAMPLES / "sample-output.json").read_text(encoding="utf-8"))
        goal = source["goal_path_design"]
        goal["intervention_path"] = goal["intervention_path"]["primary"]
        for stage, summary in zip(goal["intervention_path"]["stages"], result["activity_sequence_summary"]["stages"]):
            stage["stage_name"] = summary["stage_name"]
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

        original = result["activities"][1]
        split = []
        for index, group in enumerate(original["parallel_group_tasks"], 1):
            item = copy.deepcopy(original)
            item.update(
                activity_id=f"ACT-ST2-{index}", source_unit_ids=[group["source_unit_id"]],
                sequence_within_stage=index, activity_name=group["group_name"],
                activity_objective=group["activity_objective"],
                target_goal_ids=group["target_goal_ids"],
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


class ActivityInteractionRegressionTest(unittest.TestCase):
    def fixture(self):
        source = json.loads((SAMPLES / "sample-input.json").read_text())
        result = json.loads((SAMPLES / "sample-output.json").read_text())
        goal = copy.deepcopy(source['goal_path_design'])
        goal['intervention_path'] = goal['intervention_path']['primary']
        for stage, summary in zip(goal['intervention_path']['stages'], result['activity_sequence_summary']['stages']):
            stage['stage_name'] = summary['stage_name']
        return source, goal, result

    def test_teacher_and_material_interactions_are_valid_in_parallel_group(self):
        from backend.app.activity_formative_api import _prepare_generated_activity
        _, goal, result = self.fixture()
        group = result['activities'][1]['parallel_group_tasks'][1]
        group['dialogue_circles'] = ['师生会话圈','学生-内容会话圈','生生会话圈']
        original_text = group['student_task']
        _prepare_generated_activity(result, goal, {})
        self.assertEqual(group['student_task'], original_text)
        self.assertEqual(group['organization_form'], '同质小组干预')
        self.assertIn('学生-内容会话圈', result['activities'][1]['dialogue_circles'])

    def test_upstream_organization_overrides_model_metadata_without_changing_task(self):
        from backend.app.activity_formative_api import _prepare_generated_activity
        _, goal, result = self.fixture()
        group = result['activities'][1]['parallel_group_tasks'][1]
        group['organization_form'] = '全班集体干预'
        original = group['student_task']
        _prepare_generated_activity(result, goal, {})
        self.assertEqual(group['organization_form'], '同质小组干预')
        self.assertEqual(group['student_task'], original)

    def test_bad_timing_gets_one_semantic_repair(self):
        from unittest.mock import patch
        from backend.app.activity_formative_api import _generate_validated_activity
        source, goal, result = self.fixture()
        bad = copy.deepcopy(result)
        bad['activities'][0]['duration_minutes'] += 1
        with patch('backend.app.activity_formative_api.run_activity_formative_design',side_effect=[bad, result]) as model:
            fixed = _generate_validated_activity(None,source,goal,'mocked')
        self.assertEqual(model.call_count,2)
        self.assertIn('时长',model.call_args.kwargs['validation_feedback'])
        _validate(fixed, goal)

    def test_repair_is_bounded_and_invalid_output_is_not_accepted(self):
        from unittest.mock import patch
        from backend.app.activity_formative_api import _generate_validated_activity
        source, goal, result = self.fixture()
        result['activities'][0]['duration_minutes'] += 1
        with patch('backend.app.activity_formative_api.run_activity_formative_design',return_value=result) as model:
            with self.assertRaisesRegex(ValueError,'自动校正后仍未满足'):
                _generate_validated_activity(None,source,goal,'mocked')
        self.assertEqual(model.call_count,2)

    def test_explicit_no_ai_constraint_is_not_relaxed(self):
        from backend.app.activity_formative_api import _prepare_generated_activity
        _, goal, result = self.fixture()
        result['activities'][0]['dialogue_circles'] = ['师生机会话圈']
        with self.assertRaisesRegex(ValueError,'教师明确不使用AI'):
            _prepare_generated_activity(result,goal,{'ai_access':'none'})


if __name__ == "__main__":
    unittest.main()
