#!/usr/bin/env python3
"""Validate Skill 3 schemas, fixtures, and integration invariants."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
INPUT_SCHEMA = ROOT / "assets" / "plan-integration-input.schema.json"
OUTPUT_SCHEMA = ROOT / "assets" / "plan-integration-output.schema.json"

RULE_ITEMS = [
    ("R1", "诊断—目标一致性"),
    ("R2", "目标—活动一致性"),
    ("R3", "路径—活动一致性"),
    ("R4", "差异化合理性"),
    ("R5", "支架适切性"),
    ("R6", "活动—形成性评价一致性"),
    ("R7", "师生机角色合理性"),
    ("R8", "课堂可实施性"),
]

EXPECTED_CASES = {
    "R1-DIAGNOSIS-GOAL-JUMP",
    "R2-M-TO-R-WITH-MORE-FACTS",
    "R3-PATH-NODE-MISMATCH",
    "R4-DIFFICULTY-ONLY",
    "R5-SCAFFOLD-GIVES-RELATION",
    "R6-CORRECTNESS-ONLY",
    "R7-AI-WITHOUT-GAIN",
    "R8-TIME-OVERFLOW",
}

ALLOWED_CIRCLES = {
    "全班集体干预": {"师生会话圈", "师生机会话圈"},
    "同质小组干预": {"生生会话圈", "师生机会话圈"},
    "异质小组干预": {"生生会话圈", "师生机会话圈"},
    "个体干预": {"师生会话圈", "生机会话圈", "学生-内容会话圈"},
}

PROGRESSION_GOAL_NOTE = (
    "表中“目标层级—面向学生”的对应，是依据当前诊断为学生确定的最近发展目标，"
    "用于安排差异化支持；并不表示学生在本节课中只能达到该层级。学生达到当前目标后，"
    "可以继续向更高层次进阶，教师应根据形成性评价动态调整目标与支持。"
)

PRECISION_CONTEXT_KEYS = [
    "subject",
    "grade",
    "textbook_version_and_chapter",
    "precision_teaching_topic",
    "precision_teaching_goals",
    "precision_teaching_content",
]

TEACHER_CONTEXT_REQUIRED_KEYS = [
    "teaching_content_and_curriculum_analysis",
    "teaching_focus_and_difficulty_analysis",
    "planned_duration_minutes",
    "teaching_environment_and_ai_support_conditions",
]
TEACHER_CONTEXT_OPTIONAL_KEYS = [
    "teacher_lesson_conception",
]


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def fail(message: str) -> None:
    raise AssertionError(message)


def validate_schema(instance, schema_path: Path, label: str) -> None:
    schema = load_json(schema_path)
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(instance),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        details = "\n".join(
            f"- {'.'.join(map(str, error.absolute_path)) or '<root>'}: {error.message}"
            for error in errors
        )
        fail(f"{label} schema validation failed:\n{details}")


def validate_roster(source):
    summary = source["class_diagnosis_result"]["class_summary"]
    distribution = summary["distribution"]
    if [row["level"] for row in distribution] != ["P", "U", "M", "R", "EA"]:
        fail("Class distribution must list P, U, M, R, EA in order.")

    roster = {}
    counted = 0
    for row in distribution:
        if row["count"] != len(row["students"]):
            fail(f"Distribution count mismatch for {row['level']}.")
        counted += row["count"]
        for student in row["students"]:
            sid = student["student_id"]
            if sid in roster:
                fail(f"Duplicate diagnosis student_id: {sid}")
            roster[sid] = row["level"]
    if counted != summary["total_students"] or len(roster) != summary["total_students"]:
        fail("Diagnosis distribution does not reconcile with total_students.")
    return roster


def validate_contexts_and_task(source, result):
    if list(source["precision_teaching_context"]) != PRECISION_CONTEXT_KEYS:
        fail("precision_teaching_context must use the canonical field dictionary and order.")
    teacher_keys = set(source["teacher_instructional_context"])
    required_teacher_keys = set(TEACHER_CONTEXT_REQUIRED_KEYS)
    allowed_teacher_keys = required_teacher_keys | set(TEACHER_CONTEXT_OPTIONAL_KEYS)
    if not required_teacher_keys.issubset(teacher_keys) or not teacher_keys.issubset(allowed_teacher_keys):
        fail("teacher_instructional_context must use the canonical required fields and optional teacher_lesson_conception.")

    plan = result["integrated_plan"]
    context = source["precision_teaching_context"]
    basic = plan["basic_information"]
    for key in PRECISION_CONTEXT_KEYS:
        if basic[key] != context[key]:
            fail(f"Integrated basic information changed {key}.")

    if plan["teaching_context_and_conditions"] != source["teacher_instructional_context"]:
        fail("Integrated teaching context must preserve the canonical teacher context exactly.")

    source_task = source["diagnostic_task_context"]
    output_task = plan["diagnosis_summary"]["diagnostic_task"]
    for key in (
        "task_id",
        "task_title",
        "task_text",
        "task_materials_summary",
        "response_mode",
        "task_requirements",
        "source_ref",
    ):
        if output_task[key] != source_task[key]:
            fail(f"Integrated diagnostic task changed {key}.")
    expected_display = "查看诊断任务" if source_task["source_ref"] else "未提供"
    if output_task["source_display"] != expected_display:
        fail("Diagnostic task source display does not match source_ref availability.")

    summary = plan["diagnosis_summary"]["overall_summary"]
    for key in (
        "distribution_overview",
        "existing_foundation",
        "common_and_layered_obstacles",
        "next_teaching_direction",
    ):
        if not summary[key].strip():
            fail(f"Class diagnosis summary is missing {key}.")
    if len(summary["teacher_facing_paragraph"].strip()) < 80:
        fail("Teacher-facing class diagnosis summary is too brief.")

    if plan["goal_design"]["progression_goal_note"] != PROGRESSION_GOAL_NOTE:
        fail("Progression goal note must use the fixed approved wording.")


def validate_missing_task_source_fallback(source, result):
    source_without_ref = json.loads(json.dumps(source, ensure_ascii=False))
    result_without_ref = json.loads(json.dumps(result, ensure_ascii=False))
    source_without_ref["diagnostic_task_context"]["source_ref"] = None
    task = result_without_ref["integrated_plan"]["diagnosis_summary"]["diagnostic_task"]
    task["source_ref"] = None
    task["source_display"] = "未提供"
    validate_schema(source_without_ref, INPUT_SCHEMA, "input without task source")
    validate_schema(result_without_ref, OUTPUT_SCHEMA, "output without task source")
    validate_contexts_and_task(source_without_ref, result_without_ref)


def validate_confirmations_and_versions(source, result):
    if source["goal_path_design"]["schema_version"] != "1.2":
        fail("Unsupported Skill 1 version.")
    if source["activity_formative_design"]["schema_version"] != "1.3":
        fail("Unsupported Skill 2 version.")
    if source["class_diagnosis_result"]["schema_version"] not in {"1.0", "1.1"}:
        fail("Unsupported class diagnosis version.")
    for key in ("goal_path_design", "activity_formative_design"):
        block = source[key]
        if block["design_status"] != "teacher_confirmed":
            fail(f"{key} must be teacher_confirmed for a ready plan.")
        if block["teacher_confirmation"]["status"] != "teacher_confirmed":
            fail(f"{key}.teacher_confirmation must be teacher_confirmed.")

    expected_versions = {
        "class_diagnosis": source["class_diagnosis_result"]["schema_version"],
        "goal_path_design": "1.2",
        "activity_formative_design": "1.3",
    }
    if result["source_versions"] != expected_versions:
        fail("Output source_versions do not match supported inputs.")


def validate_goal_and_path_links(source, result, roster):
    goal_path = source["goal_path_design"]
    goal_ids = {goal["goal_id"] for goal in goal_path["progression_goals"]}
    assignments = goal_path["student_goal_assignments"]
    assignment_ids = [item["student_id"] for item in assignments]
    if set(assignment_ids) != set(roster) or len(assignment_ids) != len(set(assignment_ids)):
        fail("Student goal assignments must cover the diagnosis roster exactly once.")
    for item in assignments:
        if item["original_level"] != roster[item["student_id"]]:
            fail(f"Goal assignment reclassifies {item['student_id']}.")
        if item["primary_goal_id"] not in goal_ids:
            fail(f"Unknown primary goal {item['primary_goal_id']}.")

    stages = goal_path["intervention_path"]["stages"]
    planned = source["teacher_instructional_context"]["planned_duration_minutes"]
    if sum(stage["duration_minutes"] for stage in stages) != planned:
        fail("Skill 1 stage duration sum differs from teacher planned duration.")
    if goal_path["intervention_path"]["total_duration_minutes"] != planned:
        fail("Skill 1 total duration differs from teacher planned duration.")

    source_stage_ids = [stage["stage_id"] for stage in stages]
    source_units = {
        unit["unit_id"]
        for stage in stages
        for unit in stage["activity_units"]
    }

    activity_design = source["activity_formative_design"]
    summaries = activity_design["activity_sequence_summary"]["stages"]
    if [row["stage_id"] for row in summaries] != source_stage_ids:
        fail("Skill 2 stage order differs from Skill 1.")
    if activity_design["activity_sequence_summary"]["total_duration_minutes"] != planned:
        fail("Skill 2 total duration differs from teacher planned duration.")

    activities = activity_design["activities"]
    used_units = [uid for activity in activities for uid in activity["source_unit_ids"]]
    if set(used_units) != source_units or len(used_units) != len(set(used_units)):
        fail("Skill 2 must cover every Skill 1 path unit exactly once.")

    duration_by_stage = defaultdict(int)
    for activity in activities:
        duration_by_stage[activity["source_stage_id"]] += activity["duration_minutes"]
        if not set(activity["target_goal_ids"]).issubset(goal_ids):
            fail(f"{activity['activity_id']} references an unknown goal.")
        if not set(activity["target_student_ids"]).issubset(roster):
            fail(f"{activity['activity_id']} references an unknown student.")
        allowed_union = set().union(*(ALLOWED_CIRCLES[form] for form in activity["organization_forms"]))
        if not set(activity["dialogue_circles"]).issubset(allowed_union):
            fail(f"{activity['activity_id']} has a dialogue circle incompatible with its organization forms.")
        if len(activity["source_unit_ids"]) > 1 and not activity["parallel_group_tasks"]:
            fail(f"{activity['activity_id']} merges path units but lacks parallel_group_tasks.")

    source_stage_duration = {stage["stage_id"]: stage["duration_minutes"] for stage in stages}
    if dict(duration_by_stage) != source_stage_duration:
        fail("Sequential activity time within stages does not match Skill 1 stage time.")

    plan = result["integrated_plan"]
    if plan["basic_information"]["total_duration_minutes"] != planned:
        fail("Integrated plan duration differs from teacher planned duration.")
    if [stage["stage_id"] for stage in plan["overall_path"]["stages"]] != source_stage_ids:
        fail("Integrated plan stage order differs from Skill 1.")
    output_activities = plan["activities"]
    if [a["activity_id"] for a in output_activities] != [a["activity_id"] for a in activities]:
        fail("Integrated activities differ from Skill 2 activity sequence.")
    output_units = [uid for activity in output_activities for uid in activity["source_unit_ids"]]
    if output_units != used_units:
        fail("Integrated activities changed path-unit mappings.")


def validate_formative_evaluation(source, result):
    source_nodes = source["activity_formative_design"]["formative_evaluation_nodes"]
    output_nodes = result["integrated_plan"]["formative_evaluations"]
    if [item["evaluation_id"] for item in source_nodes] != [item["evaluation_id"] for item in output_nodes]:
        fail("Integrated plan changed formative evaluation nodes.")
    lessons = max(1, (source["teacher_instructional_context"]["planned_duration_minutes"] + 44) // 45)
    if len(source_nodes) > lessons * 2:
        fail("Sample contains more than two formative evaluations per 45-minute lesson.")


def validate_audit(result):
    items = result["audit_summary"]["review_items"]
    actual = [(item["rule_code"], item["check_item"]) for item in items]
    if actual != RULE_ITEMS:
        fail("Audit table must contain R1-R8 exactly once and in order.")
    for item in items:
        if item["status"] == "✓ 通过":
            if item["issue"] != "—" or item["suggestion"] != "—" or item["return_to"] != "—":
                fail(f"Passed item {item['rule_code']} must use em dashes for issue, suggestion, and return_to.")
        else:
            if "—" in (item["issue"], item["suggestion"]):
                fail(f"Non-passing item {item['rule_code']} needs a concrete issue and suggestion.")

    statuses = {item["status"] for item in items}
    if "× 需要修改" in statuses:
        expected_status = "needs_revision"
        expected_conclusion = "需要修改后使用"
    elif "△ 建议优化" in statuses:
        expected_status = "ready_with_suggestions"
        expected_conclusion = "可使用并建议优化"
    else:
        expected_status = "ready_for_use"
        expected_conclusion = "可直接使用"
    if result["integration_status"] != expected_status:
        fail("integration_status does not reflect the most severe audit result.")
    if result["audit_summary"]["overall_conclusion"] != expected_conclusion:
        fail("overall_conclusion does not reflect the audit results.")


def validate_teacher_template(source, result):
    template = (ROOT / "assets" / "teacher-facing-output-template.md").read_text(encoding="utf-8")
    headings = [
        "## 1. 精准教学基本信息",
        "## 2. 学生诊断结果",
        "## 3. 教学情境与条件",
        "## 4. 精准干预目标",
        "## 5. 学习活动设计",
        "## 6. 形成性评价",
        "## 7. 方案审核结果",
    ]
    positions = [template.find(heading) for heading in headings]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        fail("Teacher template section order is incorrect.")
    for _, item_name in RULE_ITEMS:
        if template.count(f"| {item_name} |") != 1:
            fail(f"Teacher template must show exactly one row for {item_name}.")
    if "## 干预成效评价" in template:
        fail("Teacher template must not add post-intervention outcome evaluation.")
    for hidden_teacher_section in ("对课堂教学设计的构想", "总体教学路径"):
        if hidden_teacher_section in template:
            fail(f"Teacher template must not display {hidden_teacher_section}.")
    if template.count(PROGRESSION_GOAL_NOTE) != 1:
        fail("Teacher template must contain the fixed progression goal note exactly once.")
    if template.count("### 诊断任务") != 1:
        fail("Teacher template must contain exactly one consolidated diagnostic task block.")
    for split_row in ("| 作答方式 |", "| 任务要求 |", "| 任务材料 |", "| 原任务与材料 |"):
        if split_row in template:
            fail("Teacher template must not split the diagnostic task into separate rows.")
    if "### 活动{{序号}}：{{活动名称}}（{{活动时长}}分钟；{{组织形式}}）" not in template:
        fail("Teacher template must place activity name, duration, and organization above the table.")
    if "| **活动过程** | **教师活动** | **学生活动** | **AI辅助** |" not in template:
        fail("Teacher template must place actor columns inside the activity-process row.")
    for old_activity_row in ("| 会话圈 |", "| 活动时长 |", "| 学习活动内容 |", "| 会话圈交互过程 |"):
        if old_activity_row in template:
            fail("Teacher template still contains the old activity-table structure.")

    if not source["integration_preferences"]["show_student_names"]:
        for row in result["integrated_plan"]["diagnosis_summary"]["distribution_rows"]:
            if row["students_display"] is not None:
                fail("Student names must be hidden when show_student_names is false.")

    expected_key_order = [
        "basic_information",
        "diagnosis_summary",
        "teaching_context_and_conditions",
        "goal_design",
        "overall_path",
        "activities",
        "formative_evaluations",
    ]
    if list(result["integrated_plan"]) != expected_key_order:
        fail("Integrated plan must place diagnosis before teaching conditions.")


def validate_behavior_cases():
    cases = load_json(ROOT / "tests" / "behavior-cases.json")
    case_ids = {case["id"] for case in cases}
    if case_ids != EXPECTED_CASES or len(cases) != 8:
        fail("Behavior cases must cover the eight audit rules exactly once.")


def main() -> int:
    if len(sys.argv) == 3:
        source = load_json(Path(sys.argv[1]).resolve())
        result = load_json(Path(sys.argv[2]).resolve())
        label = "external fixtures"
    elif len(sys.argv) == 1:
        source = load_json(ROOT / "tests" / "sample-input.json")
        result = load_json(ROOT / "tests" / "sample-output.json")
        label = "sample fixtures"
    else:
        fail("Usage: validate_contract.py [input.json output.json]")

    validate_schema(source, INPUT_SCHEMA, "input")
    validate_schema(result, OUTPUT_SCHEMA, "output")
    print(f"PASS: JSON schemas and {label}")

    roster = validate_roster(source)
    validate_contexts_and_task(source, result)
    validate_missing_task_source_fallback(source, result)
    validate_confirmations_and_versions(source, result)
    print("PASS: canonical contexts, diagnostic task, summary, note, versions, and roster")

    validate_goal_and_path_links(source, result, roster)
    print("PASS: diagnosis-goal-path-activity links and parallel timing")

    validate_formative_evaluation(source, result)
    print("PASS: formative evaluation preservation and frequency")

    validate_audit(result)
    print("PASS: eight-rule audit and overall status derivation")

    validate_teacher_template(source, result)
    print("PASS: teacher-facing order, privacy, and output boundary")

    validate_behavior_cases()
    print("PASS: eight behavior cases")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
