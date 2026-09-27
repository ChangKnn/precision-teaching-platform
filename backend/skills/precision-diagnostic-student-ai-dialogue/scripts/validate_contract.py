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
    "automatic_end",
}

VALID_ACTIONS = {
    "present_task",
    "clarify",
    "ask_reason",
    "ask_relation",
    "ask_elaboration",
    "reflect_and_probe",
    "invite_initial_thought",
    "decline_answer_request",
    "decline_correctness_judgment",
    "maintain_role_boundary",
    "redirect_to_task",
}

VALID_EVENTS = {
    "none",
    "answer_request",
    "correctness_request",
    "role_override_request",
    "off_topic",
    "no_initial_idea",
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
    expected = {
        "precision_teaching_context",
        "diagnostic_task",
        "ai_dialogue_contract",
        "student",
        "dialogue_state",
    }
    if set(data) != expected:
        errors.append("input top-level fields do not match the contract")
    contract = data.get("ai_dialogue_contract", {})
    if contract.get("teacher_confirmed") is not True:
        errors.append("teacher_confirmed must be true")
    if not contract.get("ai_role"):
        errors.append("ai_role is required")
    if not contract.get("required_rules") or not contract.get("prohibited_behaviors"):
        errors.append("teacher-confirmed required and prohibited rules are required")
    state = data.get("dialogue_state", {})
    mode = state.get("mode")
    message = state.get("current_student_message")
    if mode == "start" and message is not None:
        errors.append("start mode requires a null current_student_message")
    if mode == "respond" and (not isinstance(message, str) or not message.strip()):
        errors.append("respond mode requires a non-empty current_student_message")
    forbidden = collect_keys(data) & FORBIDDEN_KEYS
    if forbidden:
        errors.append(f"forbidden input keys found: {sorted(forbidden)}")
    return errors


def validate_message(message: dict, label: str) -> list[str]:
    errors: list[str] = []
    if set(message) != {"turn_id", "role", "content"}:
        errors.append(f"{label} must use the downstream transcript message shape")
    if message.get("role") not in {"assistant", "student"}:
        errors.append(f"{label} role must be assistant or student")
    if not message.get("content"):
        errors.append(f"{label} content must not be empty")
    return errors


def validate_output(data: dict, input_data: dict) -> list[str]:
    errors: list[str] = []
    forbidden = collect_keys(data) & FORBIDDEN_KEYS
    if forbidden:
        errors.append(f"forbidden output keys found: {sorted(forbidden)}")
    if data.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    if data.get("session_id") != input_data.get("dialogue_state", {}).get("session_id"):
        errors.append("output session_id must match input")
    reply = data.get("student_visible_reply", "")
    if not isinstance(reply, str) or not reply.strip():
        errors.append("student_visible_reply must be non-empty")
    if any(marker in reply for marker in ["P（", "U（", "M（", "R（", "EA（", "SOLO层级"]):
        errors.append("student reply must not contain SOLO diagnosis labels")
    record = data.get("record_append", [])
    if not isinstance(record, list) or not 1 <= len(record) <= 2:
        errors.append("record_append must contain one or two messages")
        record = []
    for index, message in enumerate(record):
        errors.extend(validate_message(message, f"record_append[{index}]"))
    state = input_data.get("dialogue_state", {})
    if state.get("mode") == "respond" and len(record) == 2:
        if record[0].get("role") != "student" or record[0].get("content") != state.get("current_student_message"):
            errors.append("respond mode must preserve the current student message verbatim")
        if record[1].get("role") != "assistant" or record[1].get("content") != reply:
            errors.append("assistant record must exactly match student_visible_reply")
    internal = data.get("internal_execution_record", {})
    if internal.get("new_answer_content_introduced") is not False:
        errors.append("new_answer_content_introduced must be false")
    if internal.get("evaluation_or_diagnosis_given") is not False:
        errors.append("evaluation_or_diagnosis_given must be false")
    if internal.get("contract_version") != input_data.get("ai_dialogue_contract", {}).get("contract_version"):
        errors.append("contract_version must match the teacher-confirmed contract")
    if internal.get("response_action") not in VALID_ACTIONS:
        errors.append("response_action is invalid")
    if internal.get("boundary_event") not in VALID_EVENTS:
        errors.append("boundary_event is invalid")
    return errors


def validate_behavior_cases(cases: list[dict]) -> list[str]:
    errors: list[str] = []
    ids: list[str] = []
    for case in cases:
        case_id = case.get("case_id", "")
        ids.append(case_id)
        if case.get("expected_action") not in VALID_ACTIONS:
            errors.append(f"{case_id}: invalid expected action")
        if case.get("expected_boundary_event") not in VALID_EVENTS:
            errors.append(f"{case_id}: invalid expected boundary event")
        if not case.get("student_message") or not case.get("student_visible_reply"):
            errors.append(f"{case_id}: message and reply are required")
        if collect_keys(case) & FORBIDDEN_KEYS:
            errors.append(f"{case_id}: forbidden dialogue-control key found")
    if len(ids) != len(set(ids)):
        errors.append("behavior case ids must be unique")
    required_cases = {"normal_relation_probe", "answer_request", "correctness_request", "role_override", "off_topic"}
    if not required_cases.issubset(set(ids)):
        errors.append("behavior cases must cover all five required situations")
    return errors


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: validate_contract.py INPUT_JSON OUTPUT_JSON BEHAVIOR_CASES_JSON")
        return 2
    input_data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    output_data = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    behavior_cases = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
    errors = validate_input(input_data)
    errors.extend(validate_output(output_data, input_data))
    errors.extend(validate_behavior_cases(behavior_cases))
    if errors:
        print("FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    print("PASS: role lock, dialogue contract, transcript compatibility, and boundary cases are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

