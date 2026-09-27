from __future__ import annotations

import json
import sys
from pathlib import Path


FORBIDDEN_KEYS = {
    "design_constraints",
    "max_followups",
    "max_ai_followups",
    "max_dialogue_turns",
    "turn_budget",
    "stop_conditions",
    "end_conditions",
}


def collect_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        keys = set(value)
        for child in value.values():
            keys.update(collect_keys(child))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for child in value:
            keys.update(collect_keys(child))
        return keys
    return set()


def validate_input(data: dict) -> list[str]:
    errors: list[str] = []
    if set(data) != {"precision_teaching_context"}:
        errors.append("input must contain only precision_teaching_context")
    context = data.get("precision_teaching_context", {})
    required = {
        "subject",
        "grade",
        "textbook_version_and_chapter",
        "precision_teaching_topic",
        "precision_teaching_goals",
        "precision_teaching_content",
    }
    if set(context) != required:
        errors.append("precision_teaching_context fields do not match the contract")
    return errors


def validate_output(data: dict) -> list[str]:
    errors: list[str] = []
    found_forbidden = collect_keys(data) & FORBIDDEN_KEYS
    if found_forbidden:
        errors.append(f"forbidden keys found: {sorted(found_forbidden)}")
    if data.get("schema_version") != "2.0":
        errors.append("schema_version must be 2.0")
    design = data.get("diagnostic_task_design", {})
    candidates = design.get("candidates", [])
    if not isinstance(candidates, list) or len(candidates) != 3:
        errors.append("exactly three diagnostic task candidates are required")
        candidates = []
    candidate_ids: list[str] = []
    candidate_names: list[str] = []
    for index, candidate in enumerate(candidates, start=1):
        expected_id = f"candidate_{index}"
        if candidate.get("candidate_id") != expected_id:
            errors.append(f"candidate {index} must use id {expected_id}")
        candidate_ids.append(candidate.get("candidate_id", ""))
        candidate_names.append(candidate.get("name", ""))
        duration = candidate.get("estimated_duration_minutes")
        if not isinstance(duration, int) or not 10 <= duration <= 30:
            errors.append(f"candidate {index} duration must be an integer from 10 to 30")
        task_content = candidate.get("task_content", "")
        if not isinstance(task_content, str) or len(task_content.strip()) < 20:
            errors.append(f"candidate {index} task_content must be a complete paragraph")
        rules = candidate.get("dialogue_rules", {})
        if set(rules) != {"required", "prohibited"}:
            errors.append(f"candidate {index} dialogue_rules must contain required and prohibited only")
    if len(set(candidate_ids)) != 3:
        errors.append("candidate ids must be unique")
    if len(set(candidate_names)) != 3:
        errors.append("candidate names must be unique")
    comparison = design.get("comparison", [])
    if not isinstance(comparison, list) or len(comparison) != 3:
        errors.append("comparison must contain exactly three rows")
    if design.get("recommended_candidate_id") not in set(candidate_ids):
        errors.append("recommended_candidate_id must reference one of the three candidates")
    records = data.get("internal_design_records", {}).get("candidate_records", [])
    if not isinstance(records, list) or len(records) != 3:
        errors.append("exactly three internal candidate records are required")
    valid_strategies = {"自我解释", "同伴解释", "以教为学", "辩论", "其他"}
    for index, record in enumerate(records, start=1):
        if record.get("selected_externalization_strategy") not in valid_strategies:
            errors.append(f"candidate record {index} needs one valid primary strategy")
    dimensions = data.get("internal_design_records", {}).get("diversity_review", {}).get("differentiating_dimensions", [])
    if not isinstance(dimensions, list) or len(dimensions) < 2:
        errors.append("at least two substantive differentiating dimensions are required")
    return errors


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: validate_contract.py INPUT_JSON OUTPUT_JSON")
        return 2
    input_data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    output_data = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    errors = validate_input(input_data) + validate_output(output_data)
    if errors:
        print("FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    print("PASS: three-candidate contract, diversity record, and dialogue-boundary invariants are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
