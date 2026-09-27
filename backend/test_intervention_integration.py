"""Contract checks for the integrated teaching report."""

import copy
import json
import unittest
from pathlib import Path

import jsonschema

from backend.app.intervention_integration_api import (
    _apply_teacher_overrides,
    _replace_student_ids,
    _restore_confirmed_teacher_analysis,
    _validate_output,
)


SKILL = Path(__file__).parent / "skills/precision-intervention-plan-integration-review"


class InterventionIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.source = json.loads((SKILL / "tests/sample-input.json").read_text(encoding="utf-8"))
        self.result = json.loads((SKILL / "tests/sample-output.json").read_text(encoding="utf-8"))

    def test_sample_contract_and_audit(self):
        output_schema = json.loads((SKILL / "assets/plan-integration-output.schema.json").read_text(encoding="utf-8"))
        jsonschema.validate(self.result, output_schema)
        _validate_output(self.source, self.result)

    def test_accepts_current_class_diagnosis_version_without_relabeling(self):
        self.source["class_diagnosis_result"]["schema_version"] = "1.1"
        self.result["source_versions"]["class_diagnosis"] = "1.1"
        input_schema = json.loads((SKILL / "assets/plan-integration-input.schema.json").read_text(encoding="utf-8"))
        jsonschema.validate(self.source, input_schema)
        _validate_output(self.source, self.result)

    def test_rejects_mismatched_status_and_activity_duration(self):
        broken = copy.deepcopy(self.result)
        broken["integration_status"] = "needs_revision"
        with self.assertRaisesRegex(ValueError, "整体结论"):
            _validate_output(self.source, broken)
        broken = copy.deepcopy(self.result)
        broken["integrated_plan"]["activities"][0]["duration_minutes"] += 1
        with self.assertRaisesRegex(ValueError, "活动映射或时长"):
            _validate_output(self.source, broken)

    def test_restores_teacher_confirmed_analysis_without_mutating_model_output(self):
        model_output = copy.deepcopy(self.result)
        context = model_output["integrated_plan"]["teaching_context_and_conditions"]
        context["teaching_content_and_curriculum_analysis"] = "模型压缩后的教学内容分析"
        context["teaching_focus_and_difficulty_analysis"] = "模型压缩后的重难点分析"

        corrected = _restore_confirmed_teacher_analysis(self.source, model_output)
        self.assertEqual(
            corrected["integrated_plan"]["teaching_context_and_conditions"],
            self.source["teacher_instructional_context"],
        )
        self.assertEqual(
            model_output["integrated_plan"]["teaching_context_and_conditions"]["teaching_content_and_curriculum_analysis"],
            "模型压缩后的教学内容分析",
        )
        _validate_output(self.source, corrected)

    def test_rejects_changes_to_actual_teaching_conditions(self):
        for key in (
            "planned_duration_minutes",
            "teaching_environment_and_ai_support_conditions",
            "teacher_lesson_conception",
        ):
            with self.subTest(key=key):
                model_output = copy.deepcopy(self.result)
                model_output["integrated_plan"]["teaching_context_and_conditions"][key] = "被模型改动"
                with self.assertRaisesRegex(ValueError, "报告改写了教师确认的教学条件"):
                    _restore_confirmed_teacher_analysis(self.source, model_output)

    def test_restores_omitted_optional_null_condition(self):
        self.source["teacher_instructional_context"]["teacher_lesson_conception"] = None
        model_output = copy.deepcopy(self.result)
        model_output["integrated_plan"]["teaching_context_and_conditions"].pop("teacher_lesson_conception", None)
        corrected = _restore_confirmed_teacher_analysis(self.source, model_output)
        self.assertIsNone(corrected["integrated_plan"]["teaching_context_and_conditions"]["teacher_lesson_conception"])

    def test_rejects_omitted_nonempty_condition(self):
        model_output = copy.deepcopy(self.result)
        model_output["integrated_plan"]["teaching_context_and_conditions"].pop("planned_duration_minutes")
        with self.assertRaisesRegex(ValueError, "报告改写了教师确认的教学条件"):
            _restore_confirmed_teacher_analysis(self.source, model_output)

    def test_teacher_edits_are_used_and_student_ids_are_aliased(self):
        activity = self.source["activity_formative_design"]["activities"][0]
        edited = _apply_teacher_overrides(self.source["activity_formative_design"], {
            activity["activity_id"]: {"objective": "教师修改的目标", "student": "独立写出理由", "product": "文字成果"}
        })
        item = edited["activities"][0]
        self.assertEqual(item["activity_objective"], "教师修改的目标")
        self.assertEqual(item["learning_product"], "文字成果")
        self.assertIn("独立写出理由", [action["action"] for action in item["participant_actions"]])
        replaced = _replace_student_ids({"student_id": "private-id", "nested": ["private-id"]}, {"private-id": "student-1"})
        self.assertEqual(replaced, {"student_id": "student-1", "nested": ["student-1"]})


if __name__ == "__main__":
    unittest.main()
