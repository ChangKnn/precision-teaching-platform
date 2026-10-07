#!/usr/bin/env python3
"""Validate Skill 3 schemas, fixtures, and aggregation invariants."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from aggregate_distribution import LEVELS, LEVEL_NAMES, aggregate


ROOT = Path(__file__).resolve().parents[1]
INPUT_SCHEMA_PATH = ROOT / "assets" / "class-diagnosis-input.schema.json"
OUTPUT_SCHEMA_PATH = ROOT / "assets" / "class-diagnosis-output.schema.json"
OUTPUT_TEMPLATE_PATH = ROOT / "assets" / "class-diagnosis-output-template.md"
SAMPLE_INPUT_PATH = ROOT / "tests" / "sample-input.json"
SAMPLE_OUTPUT_PATH = ROOT / "tests" / "sample-output.json"
BEHAVIOR_CASES_PATH = ROOT / "tests" / "behavior-cases.json"
UPSTREAM_SCHEMA_PATH = (
    ROOT.parent
    / "solo-student-diagnosis-feedback"
    / "assets"
    / "diagnosis-output.schema.json"
)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def validate_with_jsonschema(instance, schema, label: str) -> None:
    try:
        import jsonschema
    except ImportError as exc:
        raise RuntimeError("jsonschema is required for contract validation") from exc
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(instance=instance, schema=schema)
    print(f"PASS schema: {label}")


def student_ids(items: list[dict]) -> list[str]:
    return [item["student_id"] for item in items]


def assert_unique(values: list[str], label: str) -> None:
    require(len(values) == len(set(values)), f"duplicate values in {label}")


def main() -> None:
    input_schema = load_json(INPUT_SCHEMA_PATH)
    output_schema = load_json(OUTPUT_SCHEMA_PATH)
    sample_input = load_json(SAMPLE_INPUT_PATH)
    sample_output = load_json(SAMPLE_OUTPUT_PATH)
    behavior_cases = load_json(BEHAVIOR_CASES_PATH)
    upstream_schema = load_json(UPSTREAM_SCHEMA_PATH)

    validate_with_jsonschema(sample_input, input_schema, "sample-input.json")
    validate_with_jsonschema(sample_output, output_schema, "sample-output.json")
    for result in sample_input["student_diagnosis_results"]:
        validate_with_jsonschema(
            result,
            upstream_schema,
            f"Skill 2 result {result['student']['student_id']}",
        )

    input_results = sample_input["student_diagnosis_results"]
    input_ids = [result["student"]["student_id"] for result in input_results]
    input_names = {
        result["student"]["student_id"]: result["student"]["student_name"]
        for result in input_results
    }
    input_levels = {
        result["student"]["student_id"]: result["diagnosis"]["level"]
        for result in input_results
    }
    input_evidence = {
        result["student"]["student_id"]: {
            evidence["ref"]: evidence for evidence in result["diagnosis"]["evidence"]
        }
        for result in input_results
    }
    assert_unique(input_ids, "sample input student ids")

    computed = aggregate(sample_input)
    summary = sample_output["class_summary"]
    require(summary["total_students"] == len(input_results), "total student count mismatch")
    require(summary["total_students"] == computed["total_students"], "aggregate total mismatch")
    require(
        [item["level"] for item in summary["distribution"]] == list(LEVELS),
        "distribution must use P/U/M/R/EA order",
    )

    all_distribution_ids: list[str] = []
    for actual, expected in zip(summary["distribution"], computed["distribution"]):
        require(actual["level"] == expected["level"], "distribution level mismatch")
        require(actual["level_name"] == LEVEL_NAMES[actual["level"]], "level name mismatch")
        require(actual["level_meaning"].strip(), "level meaning is required")
        require(actual["count"] == expected["count"], f"count mismatch for {actual['level']}")
        require(
            abs(actual["proportion"] - expected["proportion"]) < 1e-12,
            f"proportion mismatch for {actual['level']}",
        )
        require(
            student_ids(actual["students"]) == student_ids(expected["students"]),
            f"student list mismatch for {actual['level']}",
        )
        all_distribution_ids.extend(student_ids(actual["students"]))
    assert_unique(all_distribution_ids, "distribution")
    require(set(all_distribution_ids) == set(input_ids), "distribution must cover every input student")

    nonempty_levels = {
        item["level"] for item in summary["distribution"] if item["count"] > 0
    }
    analysis_levels = {item["level"] for item in sample_output["level_analyses"]}
    require(analysis_levels == nonempty_levels, "level analyses must cover nonempty levels only")
    for analysis in sample_output["level_analyses"]:
        level = analysis["level"]
        expected_ids = {sid for sid, original in input_levels.items() if original == level}
        actual_ids = set(student_ids(analysis["students"]))
        require(actual_ids == expected_ids, f"level analysis students mismatch for {level}")
        require(analysis["count"] == len(actual_ids), f"level analysis count mismatch for {level}")
        for evidence in analysis["typical_evidence"]:
            sid = evidence["student_id"]
            require(sid in expected_ids, f"evidence student is outside level {level}: {sid}")
            require(evidence["student_name"] == input_names[sid], f"evidence name mismatch: {sid}")
            upstream = input_evidence[sid].get(evidence["source_ref"])
            require(upstream is not None, f"missing upstream evidence ref for {sid}")
            require(evidence["quote"] == upstream["quote"], f"quote drift for {sid}")

    trace = sample_output["traceability"]
    require(trace["input_student_count"] == len(input_ids), "trace input count mismatch")
    require(set(trace["output_student_ids"]) == set(input_ids), "trace output ids mismatch")
    assignments = {
        item["student_id"]: item["original_level"] for item in trace["level_assignments"]
    }
    require(assignments == input_levels, "Skill 2 levels were changed")
    require(trace["levels_unchanged"] is True, "levels_unchanged must be true")

    for scheme_name in ("homogeneous_groups", "heterogeneous_groups"):
        scheme_members: list[str] = []
        for group in sample_output["grouping_recommendations"][scheme_name]:
            ids = student_ids(group["students"])
            assert_unique(ids, f"{scheme_name}/{group['group_id']}")
            scheme_members.extend(ids)
        assert_unique(scheme_members, scheme_name)
        if scheme_name == "homogeneous_groups" or sample_output["grouping_recommendations"][scheme_name]:
            require(set(scheme_members) == set(input_ids), f"{scheme_name} must cover every input student")
        for group in sample_output["grouping_recommendations"][scheme_name]:
            require(bool(group["group_name"].strip()), f"{scheme_name} group name is required")
    for item in sample_output["intervention_design_basis"]:
        targets = student_ids(item["target_students"])
        assert_unique(targets, f"intervention design basis {item['basis_id']}")
        require(set(targets).issubset(input_ids), "unknown intervention design target")

    case_ids = {case["id"] for case in behavior_cases}
    require(
        case_ids
        == {
            "COUNT-INCLUDING-ZERO-LEVELS",
            "NO-RECLASSIFICATION",
            "GROUP-BY-OBSTACLE-AND-GOAL",
            "HETEROGENEOUS-OPTIONAL",
            "HETEROGENEOUS-COMPLEMENT-NOT-LEVEL-QUOTA",
            "HETEROGENEOUS-SAME-TASK-COMPACT",
            "HETEROGENEOUS-DIFFERENT-TASKS",
            "INTERVENTION-DESIGN-HANDOFF-NOT-ACTIVITY",
        },
        "behavior cases do not cover the planned boundaries",
    )
    count_case = next(case for case in behavior_cases if case["id"] == "COUNT-INCLUDING-ZERO-LEVELS")
    actual_counts = {item["level"]: item["count"] for item in computed["distribution"]}
    require(actual_counts == count_case["expected_counts"], "zero-level behavior case mismatch")

    duplicate_input = copy.deepcopy(sample_input)
    duplicate_input["student_diagnosis_results"].append(copy.deepcopy(input_results[0]))
    try:
        aggregate(duplicate_input)
    except ValueError as exc:
        require("duplicate student_id" in str(exc), "wrong duplicate-id error")
    else:
        raise AssertionError("duplicate student_id must fail")

    mismatch_input = copy.deepcopy(sample_input)
    mismatch_input["student_diagnosis_results"][0]["progression"]["current_level"] = "U"
    try:
        aggregate(mismatch_input)
    except ValueError as exc:
        require("inconsistent upstream level" in str(exc), "wrong level-mismatch error")
    else:
        raise AssertionError("inconsistent upstream level must fail")

    output_template = OUTPUT_TEMPLATE_PATH.read_text(encoding="utf-8")
    require("SOLO 层级及含义" in output_template, "template must explain SOLO labels")
    require("{{task_specific_level_meaning}}" in output_template, "level meaning placeholder missing")
    require("完整教学流程" not in output_template, "template must not become a lesson plan")
    require("共同任务、合作规则和共同产出只能出现一次" in output_template, "same-task compact presentation rule missing")
    require("组内互补依据" in output_template, "heterogeneous complementarity column missing")
    require("不得按 SOLO 层级机械配额" in output_template, "anti-quota rule missing")
    require("后续干预设计依据" in output_template, "downstream intervention design handoff missing")
    for forbidden in ["活动建议：", "组织建议：", "可行性说明：", "课堂精准教学干预建议"]:
        require(forbidden not in output_template, f"removed intervention field remains in template: {forbidden}")

    required_paths = [
        ROOT / "references" / "solo-progression-support-rules.md",
        ROOT / "references" / "grouping-rules.md",
        ROOT / "examples" / "cross-disciplinary-example.md",
        ROOT / "scripts" / "aggregate_distribution.py",
        ROOT / "tests" / "behavior-cases.json",
    ]
    require(all(path.exists() for path in required_paths), "referenced resource is missing")
    skill_text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    require("[TODO" not in skill_text, "unfinished scaffold placeholder in SKILL.md")
    require("重新判断层级" in skill_text, "no-reclassification boundary missing")

    print("PASS aggregation: counts, proportions, five rows, and student coverage")
    print("PASS upstream contract: all sample results conform to Skill 2 schema")
    print("PASS no reclassification: all individual levels preserved")
    print("PASS traceability: typical evidence quotes match upstream results")
    print("PASS grouping: members valid, optional schemes remain separate")
    print("PASS behavior boundaries: 8 cases")
    print("PASS duplicate-id and inconsistent-level rejection")
    print("PASS resources and teacher-readable output template")


if __name__ == "__main__":
    main()
