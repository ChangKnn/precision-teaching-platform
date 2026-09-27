#!/usr/bin/env python3
"""Validate the Skill 1 contract and shared precision_teaching_context fields."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = ROOT.parent
INPUT_SCHEMA_PATH = ROOT / "assets" / "task-rubric-input.schema.json"
OUTPUT_SCHEMA_PATH = ROOT / "assets" / "task-specific-solo-rubric.schema.json"
SAMPLE_INPUT_PATH = ROOT / "tests" / "sample-input.json"
INTERNAL_RUBRIC_PATH = ROOT / "references" / "general-solo-rubric.md"
SKILL2_INPUT_SCHEMA_PATH = SKILLS_ROOT / "solo-student-diagnosis-feedback" / "assets" / "diagnosis-input.schema.json"
SKILL3_INPUT_SCHEMA_PATH = SKILLS_ROOT / "solo-class-diagnosis-intervention" / "assets" / "class-diagnosis-input.schema.json"
GOAL_PATH_INPUT_SCHEMA_PATH = SKILLS_ROOT / "precision-intervention-goal-path-design" / "assets" / "goal-path-input.schema.json"
ACTIVITY_INPUT_SCHEMA_PATH = SKILLS_ROOT / "precision-intervention-activity-formative-regulation" / "assets" / "activity-formative-input.schema.json"
INTEGRATION_INPUT_SCHEMA_PATH = SKILLS_ROOT / "precision-intervention-plan-integration-review" / "assets" / "plan-integration-input.schema.json"


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    try:
        import jsonschema
    except ImportError as exc:
        raise RuntimeError("jsonschema is required for contract validation.") from exc

    input_schema = load_json(INPUT_SCHEMA_PATH)
    output_schema = load_json(OUTPUT_SCHEMA_PATH)
    sample_input = load_json(SAMPLE_INPUT_PATH)
    skill2_input_schema = load_json(SKILL2_INPUT_SCHEMA_PATH)

    jsonschema.Draft202012Validator.check_schema(input_schema)
    jsonschema.validate(instance=sample_input, schema=input_schema)
    print("PASS schema: Skill 1 sample input")

    require("general_solo_rubric" not in input_schema["required"], "general_solo_rubric must not be required at runtime.")
    require("general_solo_rubric" not in input_schema["properties"], "general_solo_rubric must not remain a runtime input property.")
    require("general_solo_rubric" not in sample_input, "Sample input still contains general_solo_rubric.")
    internal_rubric = INTERNAL_RUBRIC_PATH.read_text(encoding="utf-8")
    for anchor in ("G-P", "G-U", "G-M", "G-R", "G-EA"):
        require(anchor in internal_rubric, f"Internal rubric is missing {anchor}.")
    print("PASS internal knowledge: general SOLO rubric is embedded with five anchors")

    contexts = {
        "rubric builder": input_schema["properties"]["precision_teaching_context"],
        "student diagnosis": skill2_input_schema["properties"]["precision_teaching_context"],
    }
    optional_contracts = (
        ("class diagnosis", SKILL3_INPUT_SCHEMA_PATH, "$defs", "precisionTeachingContext"),
        ("goal and path", GOAL_PATH_INPUT_SCHEMA_PATH, "$defs", "precisionTeachingContext"),
        ("activity and formative evaluation", ACTIVITY_INPUT_SCHEMA_PATH, "properties", "precision_teaching_context"),
        ("plan integration", INTEGRATION_INPUT_SCHEMA_PATH, "$defs", "precisionTeachingContext"),
    )
    for label, path, section, field in optional_contracts:
        if path.exists():
            contexts[label] = load_json(path)[section][field]
    expected_fields = {
        "subject",
        "grade",
        "textbook_version_and_chapter",
        "precision_teaching_topic",
        "precision_teaching_goals",
        "precision_teaching_content",
    }
    for label, context in contexts.items():
        require(set(context["required"]) == expected_fields, f"{label} required context fields differ.")
        require(set(context["properties"]) == expected_fields, f"{label} context properties differ.")
    print(f"PASS shared contract: {len(contexts)} installed Skills use the same precision_teaching_context fields")

    require("precision_teaching_context" in input_schema["required"], "Skill 1 precision_teaching_context must be required.")
    require("teaching_context" not in input_schema["properties"], "Legacy teaching_context must not remain an input property.")
    require("teacher_context" not in input_schema["properties"], "Legacy teacher_context must not remain an input property.")
    require(output_schema["properties"]["schema_version"]["const"] == "1.1", "Output schema must remain compatible with version 1.1.")
    source_basis = output_schema["properties"]["source_basis"]["properties"]
    require("teacher_context_used" in source_basis, "Compatibility field teacher_context_used is missing.")
    require("precision_teaching_context" in source_basis["teacher_context_used"].get("description", ""), "Compatibility field description must point to precision_teaching_context.")
    print("PASS compatibility: output schema remains 1.1 and records context use")

    skill_text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    require("`precision_teaching_context`：必需" in skill_text, "SKILL.md must declare precision_teaching_context required.")
    require("`teacher_context`：可选" not in skill_text, "SKILL.md still declares the legacy input.")
    require("运行时不要求教师提供 `general_solo_rubric`" in skill_text, "SKILL.md must declare the rubric internal.")
    print("PASS instructions: required inputs, internal rubric, and legacy-name removal")


if __name__ == "__main__":
    main()
