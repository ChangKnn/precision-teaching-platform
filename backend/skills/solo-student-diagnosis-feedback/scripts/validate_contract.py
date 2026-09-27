#!/usr/bin/env python3
"""Validate Skill schemas, fixtures, and semantic invariants."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INPUT_SCHEMA_PATH = ROOT / "assets" / "diagnosis-input.schema.json"
OUTPUT_SCHEMA_PATH = ROOT / "assets" / "diagnosis-output.schema.json"
OUTPUT_TEMPLATE_PATH = ROOT / "assets" / "diagnosis-output-template.md"
SAMPLE_INPUT_PATH = ROOT / "tests" / "sample-input.json"
SAMPLE_OUTPUT_PATH = ROOT / "tests" / "sample-output.json"
BEHAVIOR_CASES_PATH = ROOT / "tests" / "behavior-cases.json"
UPSTREAM_SCHEMA_PATH = ROOT.parent / "solo-task-rubric-builder" / "assets" / "task-specific-solo-rubric.schema.json"


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def validate_with_jsonschema(instance, schema, label: str) -> None:
    try:
        import jsonschema
    except ImportError as exc:
        raise RuntimeError(
            "jsonschema is required for full validation. Install it in the active Python environment."
        ) from exc
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(instance=instance, schema=schema)
    print(f"PASS schema: {label}")


def main() -> None:
    input_schema = load_json(INPUT_SCHEMA_PATH)
    output_schema = load_json(OUTPUT_SCHEMA_PATH)
    sample_input = load_json(SAMPLE_INPUT_PATH)
    sample_output = load_json(SAMPLE_OUTPUT_PATH)
    behavior_cases = load_json(BEHAVIOR_CASES_PATH)
    output_template = OUTPUT_TEMPLATE_PATH.read_text(encoding="utf-8")

    validate_with_jsonschema(sample_input, input_schema, "sample-input.json")
    validate_with_jsonschema(sample_output, output_schema, "sample-output.json")
    require(UPSTREAM_SCHEMA_PATH.exists(), "Upstream solo-task-rubric-builder schema is required.")
    upstream_schema = load_json(UPSTREAM_SCHEMA_PATH)
    validate_with_jsonschema(
        sample_input["task_specific_solo_rubric"],
        upstream_schema,
        "sample-input.task_specific_solo_rubric against upstream schema 1.1",
    )

    serialized = json.dumps(output_schema, ensure_ascii=False)
    require("insufficient_evidence" not in serialized, "Output schema must not allow insufficient_evidence.")
    require(sample_output["diagnosis"]["level"] == sample_output["progression"]["current_level"], "Diagnosis and progression current levels must match.")
    require(sample_output["diagnosis"]["level"] == sample_output["traceability"]["rubric_level"], "Diagnosis and traceability rubric levels must match.")
    require(2 <= len(sample_output["strategies"]) <= 4, "Strategies count must be 2-4.")

    expected_ids = {
        "P-AI-ECHO",
        "U-ONE-VALID-POINT",
        "M-MULTIPLE-WITHOUT-RELATION",
        "R-INTEGRATED-CURRENT-CONTEXT",
        "EA-TRANSFER-AND-BOUNDARY",
    }
    actual_ids = {case["id"] for case in behavior_cases}
    require(actual_ids == expected_ids, "Behavior cases must cover the five planned boundaries.")
    require({case["expected_level"] for case in behavior_cases} == {"P", "U", "M", "R", "EA"}, "Behavior cases must cover all five SOLO levels.")

    p_case = next(case for case in behavior_cases if case["id"] == "P-AI-ECHO")
    require(p_case["expected_level"] == "P", "AI echo case must be P.")
    require(p_case["required_evidence_role"] == "non_autonomous_response", "AI echo evidence must be non-autonomous.")
    require(p_case["forbidden_result"] == "insufficient_evidence", "AI echo case must explicitly prohibit insufficient_evidence.")

    progression_map = {"P": "U", "U": "M", "M": "R", "R": "EA", "EA": "higher_EA"}
    require(progression_map[sample_output["diagnosis"]["level"]] == sample_output["progression"]["target_level"], "Sample output must follow Plus One progression.")

    required_template_fields = {
        "{{level_name}}",
        "{{current_level_plain_meaning}}",
        "{{current_level_name}}",
        "{{target_level_display}}",
        "{{target_level_name}}",
        "{{target_level_plain_meaning}}",
    }
    require(
        required_template_fields.issubset(set(part for part in required_template_fields if part in output_template)),
        "Teacher-readable output template must include level names and plain meanings for current and target levels.",
    )
    require(
        "- 当前层级：{{current_level}}\n" not in output_template
        and "- 目标层级：{{target_level}}\n" not in output_template,
        "Teacher-readable output template must not display bare SOLO codes.",
    )
    require("| 来源与定位 |" not in output_template, "Teacher-readable evidence must not use the old technical table.")
    require(
        "{{evidence_bullets_each_as_student_quote_then_this_shows}}" in output_template,
        "Teacher-readable evidence must use quote plus plain-language interpretation bullets.",
    )
    required_thinking_dimensions = {
        "{{recognized_information_summary}}",
        "{{relation_building_summary}}",
        "{{overall_organization_summary}}",
        "{{abstraction_transfer_summary}}",
    }
    require(
        all(field in output_template for field in required_thinking_dimensions),
        "Teacher-readable output must include the four thinking-structure dimensions.",
    )
    require(
        "{{thinking_structure_text_map_with_5_to_9_nodes}}" in output_template,
        "Teacher-readable output must include a text thinking-structure map.",
    )
    require(
        all(symbol in output_template for symbol in ["●", "◐", "○"]),
        "Thinking-structure map must explain established, partial, and not-yet-shown states.",
    )
    require("{{problem_bullets_2_to_4}}" in output_template, "Teacher-readable output must include multiple problem bullets.")
    require("{{observable_result}}" in output_template, "Strategies must include an observable result.")
    require(
        100 <= len(sample_output["thinking_structure_summary"]) <= 260,
        "Sample thinking structure summary must remain concise at roughly 200 Chinese characters.",
    )

    print("PASS semantic invariants: five levels, AI-echo P rule, Plus One, traceability")
    print("PASS teacher-readable report: plain evidence bullets, thinking map, four-part analysis, multiple problems, theme strategies")
    print(f"PASS total behavior cases: {len(behavior_cases)}")


if __name__ == "__main__":
    main()
