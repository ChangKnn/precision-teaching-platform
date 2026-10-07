#!/usr/bin/env python3
"""Validate Skill 1 schemas, fixtures, and teacher-facing design constraints."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
LEVELS = ["P", "U", "M", "R", "EA"]
NEXT_LEVEL = {
    "P": "U",
    "U": "M",
    "M": "R",
    "R": "EA",
    "EA": "EA_DEEPENING",
}
EXPECTED_BEHAVIOR_CASES = {
    "PATH-PROGRESSIVE",
    "PATH-MAINLINE-EMBEDDED",
    "PATH-PARALLEL-TIERED",
    "PATH-STATION-ROTATION",
    "PATH-INDIVIDUAL-ADAPTIVE",
    "ALTERNATIVE-WHEN-CLOSE",
    "NO-RECLASSIFICATION",
    "CROSS-LEVEL-GOAL-GUARD",
    "CUSTOM-GOAL-GUARD",
    "TEACHER-PREFERENCE-CONFLICT",
    "PARALLEL-DURATION",
    "ONE-GROUP-PER-45-MINUTES",
    "BOUNDARY-NO-DETAIL-OR-EVALUATION",
    "COMPOSITE-MODE-WHEN-SEQUENTIAL",
}
REMOVED_OUTPUT_KEYS = {
    "evidence_checkpoints",
    "entry_condition",
    "exit_or_merge_condition",
    "routing_rules",
    "phase_function",
    "branches",
    "traceability",
    "diagnosis_basis",
    "cognitive_change",
    "relation_to_common_goal",
    "curriculum_and_content_basis",
    "boundary_note",
}


def load_json(relative_path: str):
    with (ROOT / relative_path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_json_path(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def fail(message: str) -> None:
    raise AssertionError(message)


def validate_schema(instance, schema, label: str) -> None:
    errors = sorted(Draft202012Validator(schema).iter_errors(instance), key=lambda e: list(e.path))
    if errors:
        details = "\n".join(f"- {'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors)
        fail(f"{label} schema validation failed:\n{details}")


def collect_keys(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield key
            yield from collect_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from collect_keys(nested)


def roster_from_input(sample_input):
    diagnosis = sample_input["diagnosis_bundle"]["class_diagnosis"]
    distribution = diagnosis["class_summary"]["distribution"]
    if [item["level"] for item in distribution] != LEVELS:
        fail("Input distribution must list P, U, M, R, EA in order.")

    roster = {}
    counted = 0
    for item in distribution:
        if item["count"] != len(item["students"]):
            fail(f"Distribution count mismatch at {item['level']}.")
        counted += item["count"]
        for student in item["students"]:
            sid = student["student_id"]
            if sid in roster:
                fail(f"Duplicate input student_id: {sid}")
            roster[sid] = {
                "student_name": student["student_name"],
                "level": item["level"],
            }

    total = diagnosis["class_summary"]["total_students"]
    if counted != total or len(roster) != total:
        fail("Input distribution does not reconcile with total_students.")

    trace = {
        item["student_id"]: item["original_level"]
        for item in diagnosis["traceability"]["level_assignments"]
    }
    if set(trace) != set(roster):
        fail("Input traceability roster differs from distribution roster.")
    for sid, level in trace.items():
        if roster[sid]["level"] != level:
            fail(f"Input traceability level mismatch for {sid}.")
    return roster


def validate_goals(sample_input, sample_output, roster):
    goals = sample_output["progression_goals"]
    goal_map = {goal["goal_id"]: goal for goal in goals}
    if len(goal_map) != len(goals):
        fail("Progression goal IDs must be unique.")

    goal_student_map = {}
    for goal in goals:
        target_code = goal["target_level_code"]
        if target_code == "CUSTOM":
            if not goal["custom_goal_note"]:
                fail(f"{goal['goal_id']} is CUSTOM but lacks custom_goal_note.")
        elif goal["custom_goal_note"] is not None:
            fail(f"{goal['goal_id']} is not CUSTOM; custom_goal_note must be null.")

        for student in goal["target_students"]:
            sid = student["student_id"]
            if sid not in roster:
                fail(f"Unknown goal student: {sid}")
            expected = roster[sid]
            if student["student_name"] != expected["student_name"]:
                fail(f"Goal student name mismatch for {sid}.")
            if student["current_level"] != expected["level"]:
                fail(f"Goal current_level reclassifies {sid}.")
            if student["current_level"] not in goal["source_levels"]:
                fail(f"{goal['goal_id']} source_levels omits {sid}'s current level.")
            if target_code != "CUSTOM" and NEXT_LEVEL[student["current_level"]] != target_code:
                fail(f"{goal['goal_id']} is not adjacent progression for {sid}.")
            if sid in goal_student_map:
                fail(f"Student {sid} appears in more than one progression goal.")
            goal_student_map[sid] = goal["goal_id"]

    if set(goal_student_map) != set(roster):
        fail("Progression goals must cover every input student exactly once.")

    assignments = sample_output["student_goal_assignments"]
    if len({item["student_id"] for item in assignments}) != len(assignments):
        fail("student_goal_assignments contains duplicate students.")
    assignment_map = {item["student_id"]: item for item in assignments}
    if set(assignment_map) != set(roster):
        fail("student_goal_assignments must match the input roster exactly.")
    for sid, item in assignment_map.items():
        expected = roster[sid]
        if item["student_name"] != expected["student_name"]:
            fail(f"Assignment name mismatch for {sid}.")
        if item["original_level"] != expected["level"]:
            fail(f"Assignment reclassifies {sid}.")
        if item["primary_goal_id"] != goal_student_map[sid]:
            fail(f"Assignment goal mismatch for {sid}.")
        if item["primary_goal_id"] not in goal_map:
            fail(f"Unknown assignment goal for {sid}.")
    return goal_map


def validate_path(sample_input, sample_output, roster, goal_map):
    path = sample_output["intervention_path"]
    expected_label = "＋".join(path["primary_path_types"])
    if path["primary_path_label"] != expected_label:
        fail("primary_path_label must join primary_path_types with a full-width plus sign.")
    if len(path["primary_path_types"]) == 2 and "递进推进型" not in path["primary_path_types"]:
        fail("A two-mode combination must use progressive sequencing plus one differentiation mode.")
    stages = path["stages"]
    stage_ids = [stage["stage_id"] for stage in stages]
    if len(set(stage_ids)) != len(stage_ids):
        fail("Stage IDs must be unique.")

    unit_ids = set()
    for stage in stages:
        if stage["stage_name"] in {"共同定向", "分层活动同时开展", "分层并行加工", "共同汇聚", "综合应用"}:
            fail(f"Stage {stage['stage_id']} has a generic activity name.")
        for unit in stage["activity_units"]:
            uid = unit["unit_id"]
            if len(unit["target_goal_ids"]) != 1:
                fail(f"Activity unit {uid} must target exactly one primary cognitive level.")
            if uid in unit_ids:
                fail(f"Duplicate activity unit ID: {uid}")
            unit_ids.add(uid)
            expected_prefix = f"{stage['stage_id']}-{unit['organization_code']}"
            if not uid.startswith(expected_prefix):
                fail(f"Activity unit {uid} does not match its stage and organization code.")
            for goal_id in unit["target_goal_ids"]:
                if goal_id != "CG" and goal_id not in goal_map:
                    fail(f"Activity unit {uid} references unknown goal {goal_id}.")
            for student in unit["target_students"]:
                sid = student["student_id"]
                if sid not in roster:
                    fail(f"Activity unit {uid} references unknown student {sid}.")
                if student["student_name"] != roster[sid]["student_name"]:
                    fail(f"Activity unit {uid} has a mismatched name for {sid}.")
            if unit["organization_code"] == "H" and len(unit["target_students"]) < 2:
                fail(f"Single-student unit {uid} must use I rather than H.")
            if unit["activity_name"] in {"活动一", "活动二", "分层活动", "完成任务"}:
                fail(f"Activity unit {uid} has a generic activity name.")
            if len(stage["activity_units"]) == 1 and unit["activity_name"] != stage["stage_name"]:
                fail(f"Single-unit stage {stage['stage_id']} must use its activity name for both names.")

    planned = sample_input["teacher_instructional_context"]["planned_duration_minutes"]
    stage_total = sum(stage["duration_minutes"] for stage in stages)
    if stage_total != planned or path["total_duration_minutes"] != planned:
        fail("Distinct stage durations must sum to planned_duration_minutes.")

    parallel_stages = [stage for stage in stages if len(stage["activity_units"]) > 1]
    if not parallel_stages:
        fail("Sample output must demonstrate simultaneous activity units within one stage.")
    if planned == 45:
        group_stages = [stage for stage in stages if any(unit["organization_code"] in {"H", "X", "S"} for unit in stage["activity_units"])]
        if len(group_stages) > 1:
            fail("45-minute sample should contain only one concentrated group-work stage.")

    alternative = path["alternative_path"]
    if alternative and alternative["path_types"] == path["primary_path_types"]:
        fail("Alternative path must differ from the primary path.")
    if alternative and alternative["path_label"] != "＋".join(alternative["path_types"]):
        fail("Alternative path_label must join path_types with a full-width plus sign.")

    if "（" not in path["teacher_facing_path"] or "）" not in path["teacher_facing_path"]:
        fail("Teacher-facing path must present text names with symbols in parentheses.")


def validate_boundaries(sample_output):
    present_keys = set(collect_keys(sample_output))
    forbidden = sorted(REMOVED_OUTPUT_KEYS & present_keys)
    if forbidden:
        fail(f"Removed responsibilities still appear in output: {', '.join(forbidden)}")

    if sample_output["design_status"] != sample_output["teacher_confirmation"]["status"]:
        fail("design_status must match teacher_confirmation.status.")

    template = (ROOT / "assets" / "goal-path-output-template.md").read_text(encoding="utf-8")
    required_headers = [
        "目标层级",
        "具体目标内容",
        "相关学生姓名",
        "可观察的达成表现",
        "活动名称",
        "组织形式",
        "目标层级",
        "活动内容简介",
        "建议时长",
    ]
    for header in required_headers:
        if header not in template:
            fail(f"Teacher template is missing column: {header}")
    for phrase in ["主要组织模式", "模式说明", "课堂推进安排", "备选组织模式"]:
        if phrase not in template:
            fail(f"Teacher template is missing path label: {phrase}")
    if "同一阶段出现多行" not in template:
        fail("Teacher template must explain how simultaneous activities share a stage.")
    banned_template_phrases = [
        "课程与内容依据",
        "诊断依据",
        "目标边界",
        "目标追溯",
        "形成性证据",
        "进入条件",
        "退出或汇合条件",
        "认知功能",
        "推荐路径：",
        "文字路径：",
        "符号说明",
    ]
    for phrase in banned_template_phrases:
        if phrase in template:
            fail(f"Removed teacher-facing field remains in template: {phrase}")

    skill_text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    required_skill_phrases = [
        "precision_teaching_context",
        "不重新诊断",
        "不得显示课程依据",
        "形成性评价",
        "备选路径",
        "活动内容简介",
        "主要组织模式",
        "课堂推进安排",
    ]
    for phrase in required_skill_phrases:
        if phrase not in skill_text:
            fail(f"SKILL.md is missing required rule language: {phrase}")


def validate_behavior_cases():
    cases = load_json("tests/behavior-cases.json")
    case_ids = {case["id"] for case in cases}
    if case_ids != EXPECTED_BEHAVIOR_CASES:
        missing = sorted(EXPECTED_BEHAVIOR_CASES - case_ids)
        extra = sorted(case_ids - EXPECTED_BEHAVIOR_CASES)
        fail(f"Behavior case mismatch. Missing={missing}; extra={extra}")


def main() -> int:
    input_schema = load_json("assets/goal-path-input.schema.json")
    output_schema = load_json("assets/goal-path-output.schema.json")
    if len(sys.argv) == 3:
        sample_input = load_json_path(Path(sys.argv[1]).resolve())
        sample_output = load_json_path(Path(sys.argv[2]).resolve())
        fixture_label = "external input/output"
    elif len(sys.argv) == 1:
        sample_input = load_json("tests/sample-input.json")
        sample_output = load_json("tests/sample-output.json")
        fixture_label = "sample fixtures"
    else:
        fail("Usage: validate_contract.py [input.json output.json]")

    Draft202012Validator.check_schema(input_schema)
    Draft202012Validator.check_schema(output_schema)
    validate_schema(sample_input, input_schema, "sample-input")
    validate_schema(sample_output, output_schema, "sample-output")
    print(f"PASS: JSON schemas and {fixture_label}")

    roster = roster_from_input(sample_input)
    goal_map = validate_goals(sample_input, sample_output, roster)
    print("PASS: roster preservation and adjacent progression goals")

    validate_path(sample_input, sample_output, roster, goal_map)
    print("PASS: stage-unit path structure, parallel timing, and alternative path")

    validate_boundaries(sample_output)
    print("PASS: teacher-facing simplification and Skill 1 responsibility boundary")

    validate_behavior_cases()
    print("PASS: behavior-case inventory")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
