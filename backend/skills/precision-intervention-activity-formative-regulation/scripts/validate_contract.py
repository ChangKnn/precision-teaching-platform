#!/usr/bin/env python3
"""Validate Skill 2 input/output schemas and cross-file invariants."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
INPUT_SCHEMA = ROOT / "assets" / "activity-formative-input.schema.json"
OUTPUT_SCHEMA = ROOT / "assets" / "activity-formative-output.schema.json"
OUTPUT_TEMPLATE = ROOT / "assets" / "activity-formative-output-template.md"


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def schema_errors(instance, schema_path: Path, label: str):
    schema = load_json(schema_path)
    errors = []
    for error in Draft202012Validator(schema).iter_errors(instance):
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        errors.append(f"{label}.{location}: {error.message}")
    return errors


def collect_forbidden_keys(value, path="output"):
    forbidden = {"solo_level", "current_level", "reclassified_level", "new_solo_level"}
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in forbidden:
                found.append(f"{child_path}: Skill 2不得重新记录或修改学生层级")
            found.extend(collect_forbidden_keys(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(collect_forbidden_keys(child, f"{path}[{index}]"))
    return found


def presentation_errors():
    """Check stable teacher-facing layout invariants."""
    text = OUTPUT_TEMPLATE.read_text(encoding="utf-8")
    required = {
        "activity title": "### 活动{{activity_order}}：{{activity_name}}（{{duration_minutes}}分钟；{{organization_forms}}）",
        "objective row": "| **活动目标** |",
        "process subcolumns": "| **活动过程** | **教师活动** | **学生活动** | **AI辅助** |",
        "resources row": "| **学习材料与资源** |",
        "product row": "| **学习产出** |",
    }
    errors = []
    for label, marker in required.items():
        if marker not in text:
            errors.append(f"teacher_template.{label}: 缺少教师端固定版式标记")
    forbidden = {
        "activity name inside table": "| **活动名称** |",
        "organization form inside table": "| 组织形式 |",
        "dialogue circle inside table": "| 会话圈 |",
        "old interaction row": "| 会话圈交互过程 |",
    }
    for label, marker in forbidden.items():
        if marker in text:
            errors.append(f"teacher_template.{label}: 仍保留旧版活动表结构")
    return errors


def semantic_errors(source, result):
    errors = []
    distribution = source["diagnosis_bundle"]["class_summary"]["distribution"]
    total = source["diagnosis_bundle"]["class_summary"]["total_students"]

    distribution_ids = []
    distribution_level = {}
    for level in ("P", "U", "M", "R", "EA"):
        item = distribution[level]
        if item["count"] != len(item["student_ids"]):
            errors.append(f"input.distribution.{level}: count与student_ids数量不一致")
        for student_id in item["student_ids"]:
            distribution_ids.append(student_id)
            distribution_level[student_id] = level
    if len(distribution_ids) != total:
        errors.append("input.distribution: 五层人数之和与total_students不一致")
    if len(set(distribution_ids)) != len(distribution_ids):
        errors.append("input.distribution: 同一学生出现在多个SOLO层级")

    trace = source["diagnosis_bundle"]["traceability"]["level_assignments"]
    trace_map = {item["student_id"]: item["level"] for item in trace}
    if set(trace_map) != set(distribution_ids):
        errors.append("input.traceability: 学生名单与分布表不一致")
    for student_id, level in distribution_level.items():
        if trace_map.get(student_id) != level:
            errors.append(f"input.traceability.{student_id}: 层级与分布表不一致")

    goal_path = source["goal_path_design"]
    goals = {item["goal_id"]: item for item in goal_path["progression_goals"]}
    assignments = goal_path["student_goal_assignments"]
    assignment_map = {item["student_id"]: item["goal_id"] for item in assignments}
    if set(assignment_map) != set(distribution_ids):
        errors.append("input.student_goal_assignments: 必须覆盖且只覆盖诊断学生名单")
    for student_id, goal_id in assignment_map.items():
        if goal_id not in goals:
            errors.append(f"input.student_goal_assignments.{student_id}: 引用了未知目标{goal_id}")
        elif student_id not in goals[goal_id]["target_student_ids"]:
            errors.append(f"input.student_goal_assignments.{student_id}: 与目标学生名单不一致")

    primary = goal_path["intervention_path"]["primary"]
    stages = primary["stages"]
    if sum(stage["duration_minutes"] for stage in stages) != primary["total_duration_minutes"]:
        errors.append("input.intervention_path: 阶段时长之和与总时长不一致")
    if primary["total_duration_minutes"] != source["teacher_instructional_context"]["planned_duration_minutes"]:
        errors.append("input: 路径总时长与教师计划时长不一致")

    stage_map = {stage["stage_id"]: stage for stage in stages}
    unit_map = {}
    for stage in stages:
        for unit in stage["activity_units"]:
            if unit["unit_id"] in unit_map:
                errors.append(f"input.intervention_path: 重复活动单元{unit['unit_id']}")
            unit_map[unit["unit_id"]] = (stage, unit)

    if result["design_status"] != result["teacher_confirmation"]["status"]:
        errors.append("output: design_status与teacher_confirmation.status不一致")
    if result["activity_sequence_summary"]["total_duration_minutes"] != primary["total_duration_minutes"]:
        errors.append("output: 总时长与Skill 1不一致")

    output_stage_summaries = result["activity_sequence_summary"]["stages"]
    if [item["stage_id"] for item in output_stage_summaries] != [item["stage_id"] for item in stages]:
        errors.append("output: 阶段编号或顺序与Skill 1不一致")

    activities = result["activities"]
    activity_ids = [item["activity_id"] for item in activities]
    if len(activity_ids) != len(set(activity_ids)):
        errors.append("output.activities: activity_id重复")
    activity_map = {item["activity_id"]: item for item in activities}
    by_unit = defaultdict(list)
    by_stage = defaultdict(list)
    allowed_circles = {
        "全班集体干预": {"师生会话圈", "师生机会话圈"},
        "同质小组干预": {"生生会话圈", "师生机会话圈"},
        "异质小组干预": {"生生会话圈", "师生机会话圈"},
        "个体干预": {"师生会话圈", "生机会话圈", "学生-内容会话圈"},
    }

    for activity in activities:
        source_units = []
        for source_unit_id in activity["source_unit_ids"]:
            if source_unit_id not in unit_map:
                errors.append(f"output.{activity['activity_id']}: 引用了未知上游单元{source_unit_id}")
                continue
            source_stage, source_unit = unit_map[source_unit_id]
            if activity["source_stage_id"] != source_stage["stage_id"]:
                errors.append(f"output.{activity['activity_id']}: source_stage_id与上游单元不一致")
            source_units.append(source_unit)
            by_unit[source_unit_id].append(activity)

        expected_goals = {goal_id for item in source_units for goal_id in item["target_goal_ids"]}
        expected_students = {student_id for item in source_units for student_id in item["target_student_ids"]}
        if set(activity["target_goal_ids"]) != expected_goals:
            errors.append(f"output.{activity['activity_id']}: 目标未覆盖且只覆盖对应上游单元")
        if set(activity["target_student_ids"]) != expected_students:
            errors.append(f"output.{activity['activity_id']}: 学生未覆盖且只覆盖对应上游单元")
        permitted_activity_circles = set().union(*(allowed_circles[item] for item in activity["organization_forms"]))
        if not set(activity["dialogue_circles"]).issubset(permitted_activity_circles):
            errors.append(f"output.{activity['activity_id']}: 会话圈与组织形式不匹配")
        if len(source_units) == 1 and len(activity["organization_forms"]) != 1:
            errors.append(f"output.{activity['activity_id']}: 非并行活动必须选择一种组织形式")

        group_tasks = activity["parallel_group_tasks"]
        if len(activity["source_unit_ids"]) > 1:
            group_unit_ids = [item["source_unit_id"] for item in group_tasks]
            if len(group_unit_ids) != len(set(group_unit_ids)):
                errors.append(f"output.{activity['activity_id']}: parallel_group_tasks存在重复上游单元")
            if set(group_unit_ids) != set(activity["source_unit_ids"]):
                errors.append(f"output.{activity['activity_id']}: parallel_group_tasks未覆盖全部并行单元")
            if set(activity["organization_forms"]) != {item["organization_form"] for item in group_tasks}:
                errors.append(f"output.{activity['activity_id']}: 活动组织形式未覆盖且只覆盖各并行小组")
            for group in group_tasks:
                if group["source_unit_id"] not in unit_map:
                    continue
                _stage, unit = unit_map[group["source_unit_id"]]
                if set(group["target_goal_ids"]) != set(unit["target_goal_ids"]):
                    errors.append(f"output.{activity['activity_id']}.{group['source_unit_id']}: 目标与上游单元不一致")
                if set(group["target_student_ids"]) != set(unit["target_student_ids"]):
                    errors.append(f"output.{activity['activity_id']}.{group['source_unit_id']}: 学生与上游单元不一致")
                if not set(group["dialogue_circles"]).issubset(allowed_circles[group["organization_form"]]):
                    errors.append(f"output.{activity['activity_id']}.{group['source_unit_id']}: 会话圈与组织形式不匹配")
        elif group_tasks:
            errors.append(f"output.{activity['activity_id']}: 非并行活动不应填写parallel_group_tasks")

        orders = [item["order"] for item in activity["participant_actions"]]
        if orders != list(range(1, len(orders) + 1)):
            errors.append(f"output.{activity['activity_id']}: 会话圈交互过程必须从1连续编号")
        ai_access = source.get("teacher_activity_requirements", {}).get("ai_access", "unspecified")
        has_ai_action = any(item["actor"] == "AI" for item in activity["participant_actions"])
        has_ai_circle = any("机" in item for item in activity["dialogue_circles"])
        has_peer_action = any(item["actor"] == "同伴" for item in activity["participant_actions"])
        if ai_access == "none" and has_ai_action:
            errors.append(f"output.{activity['activity_id']}: 教师条件禁止AI但活动安排了AI")
        if has_ai_action != has_ai_circle:
            errors.append(f"output.{activity['activity_id']}: AI行动与会话圈标注不一致")
        if has_peer_action and "生生会话圈" not in activity["dialogue_circles"]:
            errors.append(f"output.{activity['activity_id']}: 出现同伴互动但未标注生生会话圈")
        by_stage[activity["source_stage_id"]].append(activity["activity_id"])

    if set(by_unit) != set(unit_map):
        missing = sorted(set(unit_map) - set(by_unit))
        errors.append(f"output.activities: 未覆盖全部上游活动单元: {missing}")
    for stage in stages:
        stage_activities = [item for item in activities if item["source_stage_id"] == stage["stage_id"]]
        sequences = sorted(item["sequence_within_stage"] for item in stage_activities)
        if sequences != list(range(1, len(stage_activities) + 1)):
            errors.append(f"output.{stage['stage_id']}: 活动序号必须从1连续编号")
        upstream_unit_ids = {item["unit_id"] for item in stage["activity_units"]}
        if len(stage["activity_units"]) > 1:
            if len(stage_activities) != 1:
                errors.append(f"output.{stage['stage_id']}: 并行单元必须合并为一个教师端活动")
            elif set(stage_activities[0]["source_unit_ids"]) != upstream_unit_ids:
                errors.append(f"output.{stage['stage_id']}: 合并活动未覆盖全部并行单元")
            elif stage_activities[0]["duration_minutes"] != stage["duration_minutes"]:
                errors.append(f"output.{stage['stage_id']}: 并行活动时长必须等于阶段时长")
        elif sum(item["duration_minutes"] for item in stage_activities) != stage["duration_minutes"]:
            errors.append(f"output.{stage['stage_id']}: 顺序活动时长之和必须等于阶段时长")

    summary_map = {item["stage_id"]: item for item in output_stage_summaries}
    for stage in stages:
        summary = summary_map.get(stage["stage_id"])
        if not summary:
            continue
        if summary["stage_name"] != stage["stage_name"] or summary["duration_minutes"] != stage["duration_minutes"]:
            errors.append(f"output.{stage['stage_id']}: 阶段名称或时长与Skill 1不一致")
        if set(summary["activity_ids"]) != set(by_stage[stage["stage_id"]]):
            errors.append(f"output.{stage['stage_id']}: 总览中的活动编号与活动设计不一致")
        if summary["simultaneous"] != (len(stage["activity_units"]) > 1):
            errors.append(f"output.{stage['stage_id']}: simultaneous未反映上游并行单元")

    all_students = set(distribution_ids)
    all_goal_ids = set(goals)
    evaluations = result["formative_evaluation_nodes"]
    recommended_max = max(2, ((primary["total_duration_minutes"] + 44) // 45) * 2)
    if len(evaluations) > recommended_max:
        errors.append(f"output.formative_evaluation_nodes: {primary['total_duration_minutes']}分钟方案通常不应超过{recommended_max}次形成性评价")
    for node in evaluations:
        if not set(node["after_activity_ids"]).issubset(activity_map):
            errors.append(f"output.{node['evaluation_id']}: 引用了未知活动")
        if not set(node["target_goal_ids"]).issubset(all_goal_ids):
            errors.append(f"output.{node['evaluation_id']}: 引用了未知目标")
        if not set(node["target_student_ids"]).issubset(all_students):
            errors.append(f"output.{node['evaluation_id']}: 引用了未知学生")
        for criterion in node["judgment_criteria"]:
            if criterion["goal_id"] not in node["target_goal_ids"]:
                errors.append(f"output.{node['evaluation_id']}: 判断标准目标不在评价目标中")

    errors.extend(collect_forbidden_keys(result))
    return errors


def main():
    if len(sys.argv) != 3:
        print("Usage: python validate_contract.py INPUT.json OUTPUT.json")
        return 2
    input_path = Path(sys.argv[1]).resolve()
    output_path = Path(sys.argv[2]).resolve()
    source = load_json(input_path)
    result = load_json(output_path)
    errors = []
    errors.extend(presentation_errors())
    errors.extend(schema_errors(source, INPUT_SCHEMA, "input"))
    errors.extend(schema_errors(result, OUTPUT_SCHEMA, "output"))
    if not errors:
        errors.extend(semantic_errors(source, result))
    if errors:
        print("CONTRACT VALIDATION FAILED")
        for item in errors:
            print(f"- {item}")
        return 1
    print("CONTRACT VALIDATION PASSED")
    print(f"- students: {source['diagnosis_bundle']['class_summary']['total_students']}")
    print(f"- upstream units: {sum(len(s['activity_units']) for s in source['goal_path_design']['intervention_path']['primary']['stages'])}")
    print(f"- output activities: {len(result['activities'])}")
    print(f"- formative evaluations: {len(result['formative_evaluation_nodes'])}")
    print(f"- duration: {result['activity_sequence_summary']['total_duration_minutes']} minutes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
