from __future__ import annotations

import json
import re
import ssl
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..config import settings
from .http import verified_ssl_context
from .llm_debug import record_llm_request
from .skills import SkillDefinition


POLISH_TEMPLATES = {
    "theme": "基本不等式应用中数量关系建模与论证思维的精准教学",
    "goal": "精准识别学生在基本不等式应用中呈现的认知结构，促进其由识别孤立数量信息，发展到整合约束、函数关系、取等条件与结论的完整论证。",
    "rationale": "本主题回应课程标准对数学建模与逻辑推理的要求，聚焦“建立约束—选择方法—说明取等条件—形成结论”的关键过程，并依据学生差异开展诊断与分层干预。",
    "diagnosis": "识别学生能否提取关键要素，建立约束与目标量之间的关系，并以取等条件为依据形成完整的最值论证。",
    "task": "请在真实情境中独立分析并说明信息提取、关系建立、求解和判断依据；AI 只使用中性追问帮助你完整表达，不提供公式、步骤或答案方向。最后提交包含模型、推理过程、取等条件和结论的论证文本。",
    "role": "中性引导者：只围绕学生已经表达的内容追问理由、关系和遗漏，不提供公式、步骤、正误判断、取等条件或答案方向。",
}


def _polish_expression(payload: dict[str, Any]) -> dict[str, Any]:
    field = str(payload.get("field", "goal"))
    original = str(payload.get("text", "")).strip()
    suggestion = POLISH_TEMPLATES.get(field)
    if not suggestion:
        suggestion = f"围绕教学目标进一步明确学习表现、关键关系与可观察证据：{original}"
    return {
        "suggestion": suggestion,
        "teacher_review_required": True,
        "notes": ["保留原意", "增加可观察的思维表现", "最终内容需由教师确认"],
    }


def _design_intervention(payload: dict[str, Any]) -> dict[str, Any]:
    duration = int(payload.get("duration_minutes", 45))
    return {
        "title": payload.get("title", "基于诊断证据的精准干预方案"),
        "duration_minutes": duration,
        "common_goal": "形成有条件、有依据的完整论证，并能在新情境中迁移关系结构。",
        "activities": [
            {"minutes": 7, "name": "比较诊断证据，明确共同问题", "organization": "全班"},
            {"minutes": 15, "name": "按当前障碍完成进阶任务", "organization": "临时分层"},
            {"minutes": 10, "name": "解释、质疑并整合关系", "organization": "异质小组"},
            {"minutes": 8, "name": "撤除支架，独立再诊断", "organization": "个体"},
            {"minutes": max(duration - 40, 1), "name": "回看变化并总结迁移结构", "organization": "全班"},
        ],
        "teacher_review_required": True,
        "guardrails": ["诊断与干预智能体分离", "不把 SOLO 结果固化为学生标签", "证据不足时先补充证据"],
    }


GENERAL_SOLO_ANCHORS = [
    {"id": "G-P", "level": "P", "definition": "尚未识别任务中的有效核心要素，或回答主要基于无关信息、错误理解、表面线索、猜测或回避。", "source_locator": "references/general-solo-rubric.md"},
    {"id": "G-U", "level": "U", "definition": "识别并使用一个与任务相关的核心要素，但没有调动其他必要要素。", "source_locator": "references/general-solo-rubric.md"},
    {"id": "G-M", "level": "M", "definition": "识别并使用多个相关要素，但要素主要并列，尚未形成明确的内在关系。", "source_locator": "references/general-solo-rubric.md"},
    {"id": "G-R", "level": "R", "definition": "通过明确关系组织多个相关要素，形成能够解释当前问题的连贯整体。", "source_locator": "references/general-solo-rubric.md"},
    {"id": "G-EA", "level": "EA", "definition": "在关联结构基础上抽象出一般原理、模型或假设，并迁移至新情境或辨析适用边界。", "source_locator": "references/general-solo-rubric.md"},
]


def _mock_task_rubric(payload: dict[str, Any]) -> dict[str, Any]:
    context = payload["precision_teaching_context"]
    task = payload["diagnostic_task"]
    task_text = task if isinstance(task, str) else task["task_text"]
    topic = context["precision_teaching_topic"]
    elements = [
        {"id": "E1", "name": "任务条件", "meaning": f"识别并准确使用“{task_text[:48]}”中与问题有关的条件。"},
        {"id": "E2", "name": "核心对象与目标", "meaning": f"明确当前任务围绕“{topic}”需要解释、判断或完成的核心目标。"},
        {"id": "E3", "name": "推理依据", "meaning": "选择能够连接任务条件与结论的概念、证据、关系或方法依据。"},
    ]
    relations = [
        {"id": "R1", "relation_statement": "E1 中的任务条件限制并支持 E2 中核心目标的表达，使回答与题目要求保持一致。", "relation_type": "条件—目标", "element_ids": ["E1", "E2"]},
        {"id": "R2", "relation_statement": "E3 中的推理依据把 E1 的条件与 E2 的判断或结论连接起来，形成可检验的完整解释。", "relation_type": "依据—结论", "element_ids": ["E1", "E2", "E3"]},
    ]
    level_data = {
        "P": ("没有形成与当前任务相关的有效回应。", "回应偏离任务，或未自主处理任何核心信息单元。", ["G-P"]),
        "U": ("能够围绕一个相关信息单元形成有效回应。", "能够依据一项任务条件或相关依据作出局部说明，但尚未兼顾其他相关方面。", ["G-U", "E1"]),
        "M": ("能够处理多个相关信息单元，但主要是并列或累加。", "能够分别说明条件、目标或推理依据，但尚未解释它们怎样共同支持结论。", ["G-M", "E1", "E2", "E3"]),
        "R": ("能够建立关键关系并形成完整的任务内解释。", "能解释任务条件如何限定判断，并用相关依据支持结论，形成连贯的整体解释。", ["G-R", "E1", "E2", "E3", "R1", "R2"]),
        "EA": ("能够在完整解释基础上进行概括、迁移或边界检验。", "先形成 R 层整体结构，再把该关系迁移到新情境或讨论其适用条件。", ["G-EA", "R2", "A1"]),
    }
    example_expressions = {
        "P": "我还没弄清这道题要解决什么，只是照着题目猜了一个答案。",
        "U": "我注意到了题目中的这个条件，我先根据它作一个判断。",
        "M": "我找到了这几个条件和方法，但还没想清楚它们怎样联系起来。",
        "R": "这些条件通过这个关系支持我的结论；换一种方法也需要说明它为什么适用。",
        "EA": "我可以把这里的关系概括为一个判断原则，用它分析新情境，并说明什么条件变化时需要调整。",
    }
    levels: dict[str, Any] = {}
    for level, (performance, evidence, trace) in level_data.items():
        levels[level] = {
            "status": "defined",
            "core_performance": [performance],
            "decision_evidence": [evidence],
            "adjacent_boundaries": [f"依据任务特定结构判断 {level} 与相邻层级的差异，不按字数或关键词数量定级。"],
            "typical_expressions": [example_expressions[level]],
            "trace_to": trace,
        }
    return {
        "schema_version": "1.1",
        "source_basis": {
            "diagnostic_task_source": "当前精准教学的诊断任务",
            "general_rubric_source": "Skill 内置 references/general-solo-rubric.md（源自《0912 SOLO 诊断量规》）",
            "general_rubric_anchors": GENERAL_SOLO_ANCHORS,
            "teacher_context_used": ["subject", "grade", "textbook_version_and_chapter", "precision_teaching_topic", "precision_teaching_goals", "precision_teaching_content"],
            "student_responses_role": "not_provided",
            "teacher_design_intent_used": bool(payload.get('diagnostic_task_design_intent', '').strip()),
            "source_uncertainties": ["当前使用 Mock AI；结构用于验证产品流程，配置真实模型后请重新生成并由教师复核。"],
        },
        "task_analysis": {
            "true_cognitive_demand": f"围绕“{topic}”，利用任务条件和推理依据形成能够回应诊断任务的完整解释或成果。",
            "elements": elements,
            "relations": {
                "items": relations,
                "whole_structure": "回答以任务核心目标为中心，用关键条件限定问题，并以明确依据把条件、过程与结论组织成完整结构。",
                "alternative_pathways": [],
            },
            "abstractions": [{
                "id": "A1",
                "kind": "transfer",
                "description": "把当前任务中形成的完整关系迁移到条件发生变化的新情境，并检验原结论是否仍成立。",
                "requires_relation_ids": ["R2"],
                "beyond_current_task": "不只完成当前题面，还能说明结构在新条件下如何保持或调整。",
                "opportunity_in_task": True,
            }],
            "task_affordance": {"highest_reasonably_elicitable_level": "EA", "limitations": [], "revision_suggestions": []},
        },
        "levels": levels,
        "usage_notes": {
            "evidence_sufficiency": ["学生证据完整性应单独记录，不作为第六个 SOLO 层级。"],
            "boundary_risks": ["不要按答案长度、关键词数量或信息数量直接定级。"],
            "downstream_instructions": ["结合 decision_evidence、adjacent_boundaries 和 trace_to 判断学生结构。"],
        },
    }


def _mock_student_diagnosis(payload: dict[str, Any]) -> dict[str, Any]:
    student = payload["student"]
    student_turn = next(item for item in payload["human_ai_dialogue"] if item["role"] == "student")
    rubric_trace = payload["task_specific_solo_rubric"]["levels"]["P"]["trace_to"]
    return {
        "schema_version": "1.0",
        "student": student,
        "evidence_sources": {
            "dialogue_used": True,
            "submitted_text_used": bool(payload.get("submitted_text")),
        },
        "diagnosis": {
            "level": "P",
            "level_name": "前结构",
            "rationale": "当前为 Mock AI 流程结果，尚未执行可信的学生思维结构判断，不能作为正式诊断结论。",
            "next_level_gap": "需要接入真实模型，依据学生自主表达和任务量规判断是否形成至少一个有效相关点。",
            "evidence": [{
                "source": "dialogue",
                "ref": student_turn["turn_id"],
                "quote": student_turn["content"],
                "interpretation": "该原话仅用于验证证据追溯展示；Mock AI 未对其认知功能作正式判断。",
                "evidence_role": "autonomous_content",
            }],
            "multi_source_note": "这是流程模拟报告；配置真实模型后必须重新生成。",
        },
        "thinking_structure_summary": "Mock AI 未执行正式思维结构分析。",
        "progression": {
            "current_level": "P",
            "target_level": "U",
            "main_obstacle": "尚未接入能够执行本 Skill 证据归属与边界判断的真实模型。",
            "concrete_goal": "配置真实模型后重新生成报告，再由教师核对学生是否自主形成了一个与任务相关的有效认知点。",
        },
        "strategies": [
            {"title": "先独立表达", "action": "下次与 AI 对话前，先用自己的话写出一个与任务直接相关的判断及理由。", "transfer_use": "需要展示自主思考过程的诊断任务"},
            {"title": "标明依据", "action": "完成判断后补充它来自哪个条件、材料或关系，避免只确认 AI 的说法。", "transfer_use": "需要证据或推理依据的学习任务"},
        ],
        "traceability": {
            "rubric_level": "P",
            "rubric_trace_ids": rubric_trace,
            "student_evidence_refs": [student_turn["turn_id"]],
        },
    }


def student_diagnosis_report_text(result: dict[str, Any]) -> str:
    diagnosis = result["diagnosis"]
    progression = result["progression"]
    evidence = "\n".join(
        f"- 学生说：\u201c{item['quote']}\u201d——{item['interpretation']}"
        for item in diagnosis["evidence"]
    )
    strategies = "\n".join(
        f"{index}. {item['title']}：{item['action']}（适用于：{item['transfer_use']}）"
        for index, item in enumerate(result["strategies"], start=1)
    )
    target_names = {"U": "单点结构", "M": "多点结构", "R": "关联结构", "EA": "拓展抽象结构", "higher_EA": "更高质量的拓展抽象结构"}
    return (
        f"## 当前情况\n\n### SOLO 水平\n{diagnosis['level']}（{diagnosis['level_name']}）：{diagnosis['rationale']}\n\n"
        f"### 判断依据\n{evidence}\n\n"
        f"### 当前障碍\n{diagnosis['next_level_gap']}\n\n{progression['main_obstacle']}\n\n"
        f"## 进阶目标\n\n{progression['target_level']}（{target_names[progression['target_level']]}）：{progression['concrete_goal']}\n\n"
        f"## 学习建议\n\n{strategies}"
    )


def _skill_runtime_instructions(skill: SkillDefinition) -> str:
    from .education_context import education_runtime_rules
    if skill.key == "precision_diagnostic_task_design":
        resource_paths = [
            "references/thinking-externalization-strategies.md",
            "references/task-design-and-quality-rules.md",
            "references/dialogue-rules.md",
        ]
    elif skill.key == "solo_student_diagnosis_feedback":
        resource_paths = ["references/diagnosis-and-ai-evidence-rules.md", "references/progression-support-rules.md"]
    elif skill.key == "solo_class_diagnosis_intervention":
        resource_paths = ["references/solo-progression-support-rules.md", "references/grouping-rules.md"]
    elif skill.key == "class_teaching_suggestions":
        resource_paths = ["references/thesis-progression-rules.md"]
    elif skill.key == "teacher_custom_analysis":
        resource_paths = []
    elif skill.key == "precision_intervention_goal_path_design":
        resource_paths = ["references/goal-design-rules.md", "references/path-selection-rules.md", "references/input-output-contract.md"]
    elif skill.key == "precision_intervention_activity_formative_regulation":
        resource_paths = [
            "references/activity-design-rules.md", "references/conversation-circle-rules.md",
            "references/formative-evaluation-rules.md", "references/input-output-contract.md",
        ]
    elif skill.key == "precision_intervention_plan_integration_review":
        resource_paths = ["references/input-output-contract.md", "references/review-rules.md", "assets/teacher-facing-output-template.md"]
    else:
        resource_paths = [
            "references/general-solo-rubric.md",
            "references/input-and-evidence-policy.md",
            "references/analysis-and-boundary-rules.md",
            "references/machine-readable-contract.md",
        ]
    sections = [skill.instructions, education_runtime_rules()]
    for relative_path in resource_paths:
        path = skill.path.parent / relative_path
        if path.is_file():
            sections.append(f"\n\n# 运行时参考：{relative_path}\n{path.read_text(encoding='utf-8')}")
    if skill.key == 'solo_class_diagnosis_intervention':
        path = skill.path.parent.parent / 'class-teaching-suggestions/references/thesis-progression-rules.md'
        if path.is_file():
            sections.append(f"\n\n# 运行时参考：教师提供的SOLO进阶设计依据\n{path.read_text(encoding='utf-8')}")
    output_contract = {
        "precision_diagnostic_task_design": "diagnostic_task_design",
        "solo_student_diagnosis_feedback": "solo_student_diagnosis",
        "solo_class_diagnosis_intervention": "solo_class_diagnosis",
        "class_teaching_suggestions": "class_teaching_suggestions",
        "teacher_custom_analysis": "teacher_custom_analysis",
        "precision_intervention_goal_path_design": "precision_intervention_goal_path",
        "precision_intervention_activity_formative_regulation": "activity_formative_design",
        "precision_intervention_plan_integration_review": "precision_intervention_plan",
    }.get(skill.key, "solo_task_rubric")
    sections.append(f"\n\n只输出符合 {output_contract} JSON Schema 的 JSON 对象，不输出 Markdown、代码围栏或额外说明。")
    return "".join(sections)


def run_diagnostic_task_design(skill: SkillDefinition, payload: dict[str, Any], provider: str) -> dict[str, Any]:
    import jsonschema
    input_schema = json.loads((skill.path.parent / 'assets/diagnostic-task-input.schema.json').read_text(encoding='utf-8'))
    jsonschema.validate(payload, input_schema)
    if provider == "mock":
        raise RuntimeError("诊断任务设计需要配置真实模型，当前仍为 Mock AI")
    result = _run_structured_skill(
        skill, payload, "diagnostic-task-output.schema.json", "diagnostic_task_design", provider,
        instruction_suffix="三个候选方案必须有实质差异，各有非空 design_intent；teacher_ideas 有值时据此设计，空白时直接依据教学情境。预计时长仅表示任务规模，不控制会话结束。",
    )
    from importlib.util import module_from_spec, spec_from_file_location

    schema = json.loads((skill.path.parent / "assets/diagnostic-task-output.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(result, schema)
    validator_path = skill.path.parent / "scripts/validate_contract.py"
    spec = spec_from_file_location("diagnostic_task_contract", validator_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("诊断任务 Skill 校验器不可用")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    errors = module.validate_input(payload) + module.validate_output(result)
    if errors:
        raise ValueError("；".join(errors))
    return result


def _extract_output_text(response: dict[str, Any]) -> str:
    if isinstance(response.get("output_text"), str) and response["output_text"].strip():
        return response["output_text"].strip()
    for item in response.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                return str(content["text"]).strip()
    raise RuntimeError("模型没有返回可用的结构化 JSON")


def _extract_chat_completion_text(response: dict[str, Any]) -> str:
    choices = response.get("choices") or []
    if choices:
        content = choices[0].get("message", {}).get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    raise RuntimeError("DeepSeek 没有返回可用的结构化 JSON")


class StructuredOutputError(ValueError):
    def __init__(self, message: str, diagnostic: str):
        super().__init__(message)
        self.diagnostic = diagnostic


def _parse_structured_json(text: str, schema: dict[str, Any], normalize=None) -> dict[str, Any]:
    """Use a complete schema-valid object even when the model appends extra text."""
    import jsonschema

    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].strip()
    decoder = json.JSONDecoder()
    last_error: Exception | None = None
    for _ in range(3):
        try:
            candidate, end = decoder.raw_decode(cleaned)
            if not isinstance(candidate, dict):
                raise ValueError("模型输出的顶层不是 JSON 对象")
            if normalize:
                candidate = normalize(candidate)
            jsonschema.validate(candidate, schema)
            return candidate
        except (json.JSONDecodeError, ValueError) as error:
            last_error = error
            break
        except jsonschema.ValidationError as error:
            last_error = error
            # Some models append a corrected second object. Inspect only a
            # directly adjacent JSON object; never extract from arbitrary prose.
            remaining = cleaned[end:].strip()
            if not remaining.startswith("{"):
                break
            cleaned = remaining
    if isinstance(last_error, jsonschema.ValidationError):
        path = '/'.join(str(part) for part in last_error.absolute_schema_path)
        diagnostic = f'格式规则 {path[:300]} 未通过'
        if last_error.validator == 'required' and isinstance(last_error.instance, dict):
            missing = [key for key in last_error.validator_value if key not in last_error.instance]
            if missing:
                diagnostic += '，缺少字段 ' + '、'.join(missing)
    elif isinstance(last_error, json.JSONDecodeError):
        diagnostic = f'JSON 不完整或格式无效（第 {last_error.lineno} 行）'
    else:
        diagnostic = '输出不是有效的 JSON 对象'
    raise StructuredOutputError(f"模型没有返回符合 Schema 的完整 JSON 对象：{last_error}", diagnostic)


def _normalize_integration_audit(result: dict) -> dict:
    """Fill only deterministic audit labels and passing-row placeholders."""
    from ..intervention_integration_api import RULE_NAMES
    import copy
    normalized = copy.deepcopy(result)
    audit = normalized.get('audit_summary')
    rows = audit.get('review_items') if isinstance(audit, dict) else None
    if not isinstance(rows, list):
        return normalized
    codes = {f'R{index}': name for index, name in enumerate(RULE_NAMES, 1)}
    inverse = {name: code for code, name in codes.items()}
    for row in rows:
        if not isinstance(row, dict):
            continue
        if not row.get('check_item') and row.get('rule_code') in codes:
            row['check_item'] = codes[row['rule_code']]
        if not row.get('rule_code') and row.get('check_item') in inverse:
            row['rule_code'] = inverse[row['check_item']]
        if row.get('status') == '✓ 通过':
            for field in ('issue', 'suggestion', 'return_to'):
                if not row.get(field):
                    row[field] = '—'
    return normalized


def _post_json(url: str, body: dict[str, Any], api_key: str, timeout: int = 120, purpose: str = "teacher_ai") -> dict[str, Any]:
    record_llm_request("teacher", purpose, settings.ai_provider, url, body)
    request = Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    deadline = time.monotonic() + timeout
    for attempt in range(2):
        try:
            response = urlopen(request, timeout=max(1, deadline - time.monotonic()), context=verified_ssl_context())
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:800]
            raise RuntimeError(f"模型请求失败（{error.code}）：{detail}") from error
        except (URLError, ssl.SSLError) as error:
            reason = getattr(error, "reason", error)
            transient_eof = isinstance(reason, ssl.SSLEOFError) or (
                isinstance(reason, ssl.SSLError) and "UNEXPECTED_EOF_WHILE_READING" in str(reason)
            )
            # Retry only TLS connection setup, once and within the original
            # timeout. Never relax certificate checks or retry HTTP rejection.
            if attempt == 0 and transient_eof and deadline - time.monotonic() > 1:
                time.sleep(0.25)
                continue
            if transient_eof:
                raise RuntimeError("模型服务的安全连接被中途断开，重连后仍未成功，请稍后重试") from error
            raise RuntimeError(f"无法连接模型服务：{reason}") from error
        # A response has started: do not automatically repeat a potentially
        # completed, billable generation if its body is interrupted.
        try:
            with response:
                return json.loads(response.read().decode("utf-8"))
        except ssl.SSLError as error:
            raise RuntimeError("模型回复传输中断，请重新生成报告；本次未保存不完整报告") from error


def _rubric_transport_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Keep shape constraints small; the original schema remains authoritative."""
    def shape(node: dict, depth: int = 0) -> dict:
        if depth > 32:
            raise ValueError('量规结构嵌套过深')
        if '$ref' in node:
            if not node['$ref'].startswith('#/'):
                raise ValueError('量规仅支持本地结构引用')
            return {'$ref': node['$ref']}
        result = {}
        kind = node.get('type')
        if kind is None:
            example = node.get('const', (node.get('enum') or [None])[0])
            if isinstance(example, bool):
                kind = 'boolean'
            elif isinstance(example, str):
                kind = 'string'
            elif isinstance(example, (int, float)):
                kind = 'number'
        if kind is not None:
            result['type'] = kind
        if 'properties' in node:
            result['properties'] = {key: shape(value, depth + 1) for key, value in node['properties'].items()}
            result['additionalProperties'] = False
        if 'required' in node:
            result['required'] = node['required']
        if 'items' in node:
            result['items'] = shape(node['items'], depth + 1)
        return result
    result = shape(schema)
    if '$defs' in schema:
        result['$defs'] = {key: shape(value) for key, value in schema['$defs'].items()}
    return result


def _rubric_format_rejected(error: Exception) -> bool:
    message = str(error).lower()
    if not re.search(r'模型请求失败（(?:400|422)）', message):
        return False
    return ('compiled grammar is too large' in message or
            ('empty schema' in message and ('not supported' in message or 'invalid schema' in message)) or
            ('grammar' in message and ('too complex' in message or 'complexity' in message)) or
            (any(word in message for word in ('response_format', 'json_schema', 'json schema', 'schema type', 'format.schema', 'structured output'))
             and any(word in message for word in ('not supported', 'unsupported', 'not available'))))


def _run_rubric_compatible(skill: SkillDefinition, payload: dict, schema: dict,
                           output_name: str, instruction_suffix: str) -> dict:
    """At most four requests: two format fallbacks and one validation repair."""
    is_rubric = skill.key == 'solo_task_rubric_builder'
    if skill.key in {'solo_student_diagnosis_feedback', 'solo_class_diagnosis_intervention'}:
        instruction_suffix += '\n面向教师或学生的分析与说明使用“对话记录”等自然表述，不在文字中展示 turn-数字、urn-数字等内部编号；结构化 ref、source_ref 等定位字段仍须准确填写，不改变学生原话。'
    label, retained = {
        'solo_task_rubric_builder': ('量规', '原量规已保留'),
        'precision_intervention_goal_path_design': ('目标与路径', '原目标与路径已保留'),
        'precision_intervention_activity_formative_regulation': ('学习活动', '原学习活动已保留'),
        'precision_intervention_plan_integration_review': ('教学方案', '原教学方案已保留'),
        'solo_class_diagnosis_intervention': ('诊断分析', '原班级报告已保留'),
    }.get(skill.key, ('诊断分析', '原诊断已保留'))
    body = {
        'model': settings.ai_model, 'max_tokens': 16000, 'stream': False,
        'messages': [
            {'role': 'system', 'content': _skill_runtime_instructions(skill) + instruction_suffix +
             '\n只输出完整JSON对象。以下完整规则用于平台校验，所有必填字段和教学要求都必须满足：\n' +
             json.dumps(schema, ensure_ascii=False)},
            {'role': 'user', 'content': f'请根据以下输入生成{label}JSON：\n' + json.dumps(payload, ensure_ascii=False)},
        ],
        'response_format': {'type': 'json_schema', 'json_schema': {
            'name': output_name, 'strict': False, 'schema': _rubric_transport_schema(schema)}},
    }
    if skill.key == 'solo_class_diagnosis_intervention' and settings.ai_model.startswith('anthropic/claude-haiku-5.5'):
        body['max_tokens'] = 32000
    is_long_plan = skill.key == 'precision_intervention_plan_integration_review' and settings.ai_model.startswith('anthropic/claude-haiku-5.5')
    if is_long_plan:
        body['max_tokens'] = 32000
    deadline, mode, repaired = time.monotonic() + (360 if is_long_plan else 180), 0, False
    for _ in range(4):
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            raise RuntimeError(f'{label}生成及格式修正已超时，{retained}，请稍后重试')
        try:
            response = _post_json(f'{settings.ai_base_url}/chat/completions', body, settings.ai_api_key,
                                  timeout=min(240 if is_long_plan else 120, remaining), purpose=skill.key)
        except RuntimeError as error:
            if mode < 2 and _rubric_format_rejected(error):
                mode += 1
                if mode == 1:
                    body['response_format'] = {'type': 'json_object'}
                else:
                    body.pop('response_format', None)
                continue
            raise
        text = ''
        try:
            text = _extract_chat_completion_text(response)
            result = _parse_structured_json(text, schema, normalize=_normalize_integration_audit if skill.key == 'precision_intervention_plan_integration_review' else None)
            if is_rubric:
                from .compact_outputs import expand_rubric
                result = expand_rubric(result)
                validate_task_rubric(skill, result, require_complete=True)
            if output_name == 'student_brief':
                _validate_student_brief_evidence(result, payload)
            if output_name in ('solo_class_diagnosis', 'class_overview'):
                if repaired:
                    _discard_unknown_common_issue_refs(result, payload)
                _validate_class_overview_refs(result, payload)
            if output_name in ('solo_class_diagnosis', 'class_level_problems'):
                _validate_class_problem_groups(result, payload)
                if output_name == 'solo_class_diagnosis' and 'grouping_recommendations' not in result:
                    from .compact_outputs import expand_large_class_output
                    expand_large_class_output(result, payload)
            return result
        except (ValueError, RuntimeError) as error:
            if repaired:
                detail = f'（{error.diagnostic}）' if skill.key in {'solo_class_diagnosis_intervention', 'precision_intervention_plan_integration_review'} and isinstance(error, StructuredOutputError) else ''
                if skill.key in {'solo_class_diagnosis_intervention', 'precision_intervention_plan_integration_review'} and (response.get('choices') or [{}])[0].get('finish_reason') == 'length':
                    detail = '（模型输出达到长度限制，JSON 未完整返回）'
                raise RuntimeError(f'{label}输出校验及一次修正仍未通过{detail}，{retained}，请重试') from error
            repaired = True
            if skill.key == 'precision_intervention_plan_integration_review' and (response.get('choices') or [{}])[0].get('finish_reason') == 'length':
                body['messages'].append({'role': 'user', 'content': '上次输出达到长度限制，JSON 不完整。请根据原始输入重新生成完整 JSON；简洁表达审核意见与总结，不重复上游原文以外的无关解释，并保留全部必填字段、目标、活动及评价节点。'})
                continue
            if skill.key == 'solo_class_diagnosis_intervention' and isinstance(error, StructuredOutputError) and error.diagnostic.startswith('JSON 不完整'):
                # A partial JSON body is not a valid assistant turn to repair;
                # do not pass a cut-off 48k prefix as if it were complete.
                body['messages'].append({'role': 'user', 'content': '上次返回的JSON不完整或格式无效。请基于原始输入重新生成一个完整JSON对象，严格闭合所有数组、对象和字符串；各字段表述简洁，保留全体学生及必要证据，不输出代码围栏或额外文字。'})
                continue
            body['messages'] += [
                {'role': 'assistant', 'content': text[:48000] or '（未返回有效JSON）'},
                {'role': 'user', 'content': f'请修正以下校验问题，重新输出完整{label}JSON，不降低原规则要求：\n' + str(error)[:1200]},
            ]
    raise RuntimeError(f'{label}生成已达到格式兼容及修正次数上限，{retained}，请重试')


def _run_deepseek_structured_skill(
    skill: SkillDefinition,
    payload: dict[str, Any],
    schema: dict[str, Any],
    output_name: str,
    instruction_suffix: str = "",
    provider: str = "deepseek",
) -> dict[str, Any]:
    instructions = (
        f"{_skill_runtime_instructions(skill)}\n\n{instruction_suffix}\n\n"
        f"输出对象名称：{output_name}。必须严格符合下面的 JSON Schema；不得省略 required 字段：\n"
        f"{json.dumps(schema, ensure_ascii=False)}"
    )
    body = {
        "model": settings.ai_model,
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": f"请根据以下输入生成 JSON：\n{json.dumps(payload, ensure_ascii=False)}"},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 16000,
        "stream": False,
    }
    if provider == "deepseek":
        # DeepSeek 的思考内容与最终 JSON 共用输出预算。
        body.update({"thinking": {"type": "enabled"}, "temperature": 0.1})
    elif provider == "openrouter":
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": output_name, "strict": False, "schema": schema},
        }
    last_error: Exception | None = None
    for attempt in range(2):
        if attempt:
            body["messages"][-1]["content"] += (
                "\n\n上一次输出为空、JSON 不完整或不符合 Schema。请压缩推理和文字表述，"
                "只输出一个完整、合法且符合全部 required 字段的 JSON 对象。"
            )
        response = _post_json(f"{settings.ai_base_url}/chat/completions", body, settings.ai_api_key, purpose=skill.key)
        try:
            text = _extract_chat_completion_text(response)
            return _parse_structured_json(text, schema)
        except (RuntimeError, ValueError) as error:
            last_error = error
    raise RuntimeError(f"模型连续两次未返回完整结构化 JSON：{last_error}") from last_error


def _run_structured_skill(
    skill: SkillDefinition,
    payload: dict[str, Any],
    schema_filename: str,
    output_name: str,
    provider: str,
    schema_override: dict[str, Any] | None = None,
    instruction_suffix: str = "",
) -> dict[str, Any]:
    if not settings.ai_api_key or not settings.ai_model:
        key_name = "OPENROUTER_API_KEY" if provider == "openrouter" else "AI_API_KEY"
        raise RuntimeError(f"真实模型尚未配置 {key_name} 或 AI_MODEL")
    schema_path = skill.path.parent / "assets" / schema_filename
    schema = schema_override or json.loads(schema_path.read_text(encoding="utf-8"))
    if provider == 'openrouter' and skill.key in {'solo_task_rubric_builder', 'solo_class_diagnosis_intervention', 'solo_student_diagnosis_feedback', 'precision_intervention_goal_path_design', 'precision_intervention_activity_formative_regulation', 'precision_intervention_plan_integration_review'}:
        return _run_rubric_compatible(skill, payload, schema, output_name, instruction_suffix)
    if provider in {"deepseek", "openrouter"}:
        return _run_deepseek_structured_skill(skill, payload, schema, output_name, instruction_suffix, provider)
    body = json.dumps(
        {
            "model": settings.ai_model,
            "instructions": _skill_runtime_instructions(skill) + instruction_suffix,
            "input": json.dumps(payload, ensure_ascii=False),
            "text": {"format": {"type": "json_schema", "name": output_name, "schema": schema, "strict": False}},
            "max_output_tokens": 8000,
            "store": False,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        f"{settings.ai_base_url}/responses",
        data=body,
        headers={"Authorization": f"Bearer {settings.ai_api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    record_llm_request("teacher", skill.key, provider, request.full_url, json.loads(body))
    try:
        with urlopen(request, timeout=120, context=verified_ssl_context()) as response:
            api_response = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"模型请求失败（{error.code}）：{detail}") from error
    except URLError as error:
        raise RuntimeError(f"无法连接模型服务：{error.reason}") from error
    text = _extract_output_text(api_response)
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def validate_task_rubric(skill: SkillDefinition, rubric: dict[str, Any], *, require_complete: bool = False) -> None:
    schema_path = skill.path.parent / "assets/task-specific-solo-rubric.schema.json"
    try:
        import jsonschema
    except ImportError as error:
        raise RuntimeError("缺少 jsonschema 依赖，无法校验任务量规") from error
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(rubric, schema)
    internal_code = re.compile(r'(?<![A-Za-z0-9_])(?:[ERA][1-9]\d*|PATH[-_ ]?[1-9]\d*|G-(?:EA|P|U|M|R))(?![A-Za-z0-9_])', re.IGNORECASE)
    teacher_fields = ('core_performance', 'decision_evidence', 'adjacent_boundaries', 'typical_expressions', 'limitations')
    for level, row in rubric['levels'].items():
        if any(internal_code.search(text) for field in teacher_fields for text in row.get(field, [])):
            raise ValueError(f'{level} 层级的教师说明含内部编号，请改为具体内容与关系的自然语言表述；追溯编号仅可留在机器字段。')
    if require_complete:
        from .manual_rubric import require_complete_levels
        require_complete_levels(rubric)
        if any(row['status'] != 'defined' for row in rubric['levels'].values()):
            raise ValueError('生成标准须完整定义五个层级；任务观察限制请写在表下备注，不代替层级标准。')


def run_teacher_custom_analysis(skill: SkillDefinition, payload: dict[str, Any], provider: str) -> dict[str, Any]:
    if skill.key != "teacher_custom_analysis":
        raise ValueError("自定义标准分析使用了错误的 Skill")
    if provider == "mock":
        result = {
            "mode": payload["mode"],
            "analyzed_student_count": len(payload["students"]),
            "summary": "当前为 Mock 模式，未进行真实分析。",
            "findings": [],
            "suggestions": [],
            "limitations": ["请配置真实模型后重新生成，勿将此预览作为学生反馈。"],
        }
    else:
        result = _run_structured_skill(
            skill, payload, "custom-analysis-output.schema.json", "teacher_custom_analysis", provider
        )
    import jsonschema
    schema = json.loads((skill.path.parent / "assets/custom-analysis-output.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(result, schema)
    if result["mode"] != payload["mode"] or result["analyzed_student_count"] != len(payload["students"]):
        raise ValueError("自定义分析的范围或人数与输入不一致")
    sources = {
        source["source_id"]: source["content"]
        for student in payload["students"] for source in student["evidence_sources"]
        if source["role"] == "student"
    }
    for finding in result["findings"]:
        for evidence in finding["evidence"]:
            source = sources.get(evidence["source_id"])
            if source is None or evidence["quote"] not in source:
                raise ValueError("自定义分析引用了不存在的学生原话")
    if any(student.get("unread_attachment_count", 0) for student in payload["students"]):
        result["limitations"].append("本次仅分析对话和最终文字成果，未读取图片或附件内容。")
    return result


def run_student_diagnosis_report(
    skill: SkillDefinition, payload: dict[str, Any], provider: str
) -> tuple[dict[str, Any], str, str]:
    if skill.key != "solo_student_diagnosis_feedback":
        raise ValueError("此接口只用于学生个体诊断 Skill")
    schema = json.loads((skill.path.parent / "assets/diagnosis-output.schema.json").read_text(encoding="utf-8"))
    if provider == "mock":
        result = _mock_student_diagnosis(payload)
        report_text = student_diagnosis_report_text(result)
        student_feedback_text = "这是流程预览，尚未完成真实诊断。请等待老师生成并确认反馈。"
    else:
        # Fragment references such as #/$defs/evidenceItem resolve from the
        # document root in provider schema compilers, including when nested.
        diagnosis_schema = {key: value for key, value in schema.items() if key not in ('$defs', '$id', '$schema')}
        envelope_schema = {
            "type": "object",
            "additionalProperties": False,
            "$defs": schema.get("$defs", {}),
            "required": ["solo_student_diagnosis", "student_feedback_markdown"],
            "properties": {
                "solo_student_diagnosis": diagnosis_schema,
                "student_feedback_markdown": {"type": "string", "minLength": 500, "maxLength": 4000},
            },
        }
        instructions = (
            "\n\n此平台只接收两个字段：solo_student_diagnosis（符合Schema的诊断JSON）和"
            "student_feedback_markdown（教师核对后可推送给学生的反馈）。"
            "不要输出teacher_report_markdown，不生成六部分教师报告，不要求思维结构图或图例符号。"
            "平台根据诊断JSON直接呈现当前情况（SOLO水平、判断依据、当前障碍）、进阶目标、学习建议。"
            "诊断理由和证据解释使用教师可读的语言，E/R/A编号仅用于traceability等机器定位。"
            "student_feedback_markdown 面向输入学段的学生：中小学用相应年龄可理解的表达，高等教育用大学课程的必要专业表达；围绕当前情况、下一步目标、学习建议组织，"
            "涵盖学生原话及解释、当前障碍、2—4条具体练习建议和检查方法；信息须与诊断JSON一致。"
            "通常约800—1500个汉字，不为凑字数重复内容。"
            "语言清楚、亲切、有行动感，不过度夸奖，也不显得幼稚；"
            "不出现 P/U/M/R/EA、前结构等层级标签、量规术语或未观察到的能力断言，不直接给诊断题答案。"
        )
        envelope = _run_structured_skill(
            skill, payload, "diagnosis-output.schema.json", "solo_student_diagnosis_report",
            provider, schema_override=envelope_schema, instruction_suffix=instructions,
        )
        result = envelope["solo_student_diagnosis"]
        report_text = student_diagnosis_report_text(result)
        student_feedback_text = envelope["student_feedback_markdown"].strip()
    try:
        import jsonschema
    except ImportError as error:
        raise RuntimeError("缺少 jsonschema 依赖，无法校验学生诊断报告") from error
    jsonschema.validate(result, schema)
    return result, report_text, student_feedback_text


def run_student_brief(skill, payload, provider):
    """Compact structured evidence, without generating a student-facing essay."""
    if provider == 'mock':
        result = _mock_student_diagnosis(payload)
    else:
        result = _run_structured_skill(skill, payload, 'diagnosis-output.schema.json', 'student_brief', provider,
            instruction_suffix='只输出结构化诊断，不生成面向学生的反馈长文。判断依据、主要障碍各用1—2句；保留1—3条学生原话证据。evidence.quote必须从对应学生发言或提交文本逐字复制连续片段，保留原有标点和空格，不得改写、拼接、补充省略号或引用AI的话；解释写在interpretation。source应准确对应dialogue或submitted_text。进阶目标一句，策略只写两条简短行动。主要障碍必须说明本题中学生具体误解了什么、遗漏了哪条依据或在哪一步解释不清，并引用学生实际表达；不能把SOLO定义或“缺少关联”直接当作障碍。原因没有证据时明确尚需了解，不猜测学生态度、心理或知识缺陷。')
    import jsonschema
    schema = json.loads((skill.path.parent/'assets/diagnosis-output.schema.json').read_text())
    jsonschema.validate(result, schema)
    if provider != 'mock':
        _validate_student_brief_evidence(result, payload)
    return result, student_diagnosis_report_text(result), ''


def _validate_student_brief_evidence(result, payload):
        if result['student']['student_id'] != payload['student']['student_id']:
            raise ValueError('诊断引用了其他学生，结果未保存')
        if len({result['diagnosis']['level'],result['progression']['current_level'],result['traceability']['rubric_level']}) != 1:
            raise ValueError('诊断层级不一致，结果未保存')
        student_texts = [m['content'] for m in payload['human_ai_dialogue'] if m['role']=='student']
        for evidence in result['diagnosis']['evidence']:
            sources = student_texts if evidence['source']=='dialogue' else [payload.get('submitted_text','')]
            if not evidence['quote'].strip() or not any(evidence['quote'] in source for source in sources):
                raise ValueError('证据quote必须逐字复制对应学生发言或提交文本中的连续原文，不得引用AI发言、改写、拼接或增加省略号；请检查source并重新输出')


def _discard_unknown_common_issue_refs(result, payload):
    """After a model repair, retain only verifiable references; never invent a match."""
    valid_ids = {student['student']['student_id'] for student in payload.get('student_diagnosis_results', [])}
    if 'common_issues' not in result:
        return
    kept, changed = [], False
    for issue in result['common_issues']:
        ids = issue.get('basis_student_ids', [])
        valid = list(dict.fromkeys(student_id for student_id in ids if student_id in valid_ids))
        changed = changed or len(valid) != len(ids)
        if valid:
            kept.append({**issue, 'basis_student_ids': valid})
        else:
            changed = True
    if changed:
        if kept:
            result['common_issues'] = kept
        else:
            result.pop('common_issues', None)
        result['common_issue_warning'] = '部分共性问题的学生依据未通过核验，未展示无法核实的内容。'


def _validate_class_overview_refs(result, payload):
    """Reject invented identifiers early so the model can correct its own output."""
    source = {student['student']['student_id']: student for student in payload.get('student_diagnosis_results', [])}
    for issue in result.get('common_issues', []):
        if not set(issue.get('basis_student_ids', [])) <= set(source):
            raise ValueError('班级共性问题引用了未知学生。请仅从输入的student.student_id逐字复制编号，不要使用姓名、序号或示例编号。')
    for alert in result.get('individual_attention', []):
        student = source.get(alert.get('student_id'))
        statements = (student or {}).get('student_statements', []) + [(student or {}).get('submitted_text', '')]
        if not student or not any(alert.get('evidence_quote', '') and alert['evidence_quote'] in text for text in statements):
            raise ValueError('个体关注须引用输入中真实学生编号，并逐字引用该学生的原始表达；无依据则不要生成该提醒。')


def _validate_class_problem_groups(result, payload):
    expected = {}
    for student in payload.get('student_diagnosis_results', []):
        expected.setdefault(student['diagnosis']['level'], set()).add(student['student']['student_id'])
    rows = result.get('level_analyses', [])
    if set(row.get('level') for row in rows) != set(expected) or len(rows) != len(expected):
        raise ValueError('问题分类须覆盖所有有学生的层级，每层仅一条层级分析')
    for row in rows:
        design = row.get('teaching_design') or {}
        if not all(str(design.get(key, '')).strip() for key in ('content', 'task', 'scaffold')):
            raise ValueError(f"{row['level']}层级须分别给出内容、任务和支架建议")
        groups = row.get('difficulty_groups') or []
        if not 1 <= len(groups) <= 2:
            raise ValueError(f"{row['level']}层级需要归纳1至2类主要问题")
        labels = set()
        for group in groups:
            if not all(str(group.get(key, '')).strip() for key in ('problem_label', 'problem_description', 'teaching_suggestion')):
                raise ValueError(f"{row['level']}层级每类问题须填写短语、描述和教学建议")
            if len(group['problem_label'].strip()) > 18:
                raise ValueError(f"{row['level']}层级问题标题须为18字以内短语")
            label = group['problem_label'].strip()
            if label in labels:
                raise ValueError(f"{row['level']}层级相同问题应合并为一类")
            labels.add(label)
            ids = set(group.get('student_ids') or [])
            if not ids or len(ids) != len(group['student_ids']) or not ids <= expected[row['level']]:
                raise ValueError(f"{row['level']}层级问题须关联本层级真实学生")


def run_class_diagnosis_report(
    skill: SkillDefinition, payload: dict[str, Any], provider: str
) -> tuple[dict[str, Any], None]:
    if skill.key != "solo_class_diagnosis_intervention":
        raise ValueError("此接口只用于班级诊断 Skill")
    if provider == "mock":
        raise RuntimeError("班级诊断需要真实模型；Mock AI 不生成正式班级报告")
    schema = json.loads((skill.path.parent / "assets/class-diagnosis-output.schema.json").read_text(encoding="utf-8"))
    from .compact_outputs import class_transport_schema, expand_class, expand_large_class_output
    compact_large = len(json.dumps(payload, ensure_ascii=False)) > 24000
    if compact_large:
        return _run_large_class_in_stages(skill, payload, provider, schema)
    compact_instruction = (
        '本次输入较长：不得输出grouping_recommendations，平台会依据问题分类和完整学生名单生成分组。'
        '典型证据只输出student_id与source_ref，必须对应该学生个体诊断中的真实ref；不要输出学生姓名、原话或解释。'
        'difficulty_groups中只写problem_label、problem_description、student_ids、group_name和teaching_suggestion，不输出重复的difficulty字段。'
        '问题分类、具体困难、教学建议及必要的个体关注均保持简洁且完整。'
    ) if compact_large else ''
    result = _run_structured_skill(
        skill, payload, "class-diagnosis-output.schema.json", "solo_class_diagnosis",
        provider, schema_override=class_transport_schema(schema, compact_large=compact_large), instruction_suffix=(
            "\n\n只返回符合 Schema 的班级结构化分析，不额外生成文字报告。"
            "教师页面与导出报告由平台根据这些字段排版。"
            "每层level_group_name必须从本层teaching_design.task的具体学习任务提炼一个2至6字的学习活动名称，表明学生要做什么，不使用学生的问题、SOLO层级名或通用层级套话；每层main_performance用2至4句概括学生实际表现和有依据的差异，区分自主提出、经AI追问后提出和仅确认AI的内容，不以少数学生代替全体。主要问题依据个体表达及本次任务，写明处理了什么、具体在哪一步受阻，不套写层级定义或推测。teaching_design分别给出content、task和scaffold，各一句，结合当前学科任务、学生障碍及SOLO进阶依据，不代学生作答。P先支持一个相关回应，U先扩展相关要素而不强求完整关系链，M按任务建立适当关系，R可根据目标和材料检验新情境或条件。不得把时间顺序或未经验证的关系写成因果结论。"
            "只能引用输入中的个体诊断，不得重新定级，也不要设计具体课堂活动。"
            "本轮不输出class_summary、traceability或level_analyses内的students/count；这些统计和完整名单由程序据全体输入生成。其他字段保持完整但简洁，每层只选1—2条典型证据，避免重复复述。"
            "common_issues归纳1至3条基于学生实际表现的共同问题，不复述SOLO层级介绍，填写有依据的学生ID。每层根据学生progression.main_obstacle优先归纳一个最值得教学关注的问题，只有学生确有明显不同且需要不同支持时才写第二个；不得为了覆盖每位学生而强行分类。每项problem_label为18字以内短语，problem_description一句话，difficulty与描述相同，student_ids只列有依据的学生，teaching_suggestion给一句具体支持。individual_attention仅记录有明确原话依据的身体不适、参与状态、情绪表达、学习支持、新思路或安全关注；只凭明确表达的感受判断情绪，不能由拒答或催促推测。evidence_quote须逐字摘自该学生原始表达；无依据时返回空数组。不得臆测心理和给学生贴标签，明确安全风险时建议教师及时关注。所有描述尽量简短。分组须覆盖全体已诊断学生。"
            + compact_instruction
        ),
    )
    if compact_large:
        result = expand_large_class_output(result, payload)
    if payload.get('student_diagnosis_results'):
        result['task_group_names_version'] = '1'
        result = expand_class(result, payload)
    try:
        import jsonschema
    except ImportError as error:
        raise RuntimeError("缺少 jsonschema 依赖，无法校验班级诊断报告") from error
    jsonschema.validate(result, schema)
    # Preserve the call signature; the caller renders the validated result locally.
    _validate_class_problem_groups(result, payload)
    result['task_group_names_version'] = '1'
    result['problem_grouping_version'] = '1.1'
    return result, None


def _run_large_class_in_stages(skill, payload, provider, full_schema):
    """Analyze each level separately, then combine with a compact class overview."""
    from concurrent.futures import ThreadPoolExecutor
    from .compact_outputs import class_transport_schema, expand_class, expand_large_class_output
    import jsonschema
    students = payload['student_diagnosis_results']
    levels = [level for level in ('P', 'U', 'M', 'R', 'EA') if any(s['diagnosis']['level'] == level for s in students)]
    group_schema = full_schema['$defs']['levelAnalysis']['properties']['difficulty_groups']
    group_schema = json.loads(json.dumps(group_schema))
    group_schema['maxItems'] = 2
    group_schema['items']['required'].remove('difficulty')
    group_schema['items']['properties'].pop('difficulty')
    level_schema = {'type':'object', 'additionalProperties':False, 'required':['level_analyses'],
        'properties':{'level_analyses':{'type':'array','minItems':1,'maxItems':1,'items':{
            'type':'object','additionalProperties':False,'required':['level','main_performance','difficulty_groups','teaching_design'],
            'properties':{'level':{'type':'string','enum':levels},'level_group_name':full_schema['$defs']['levelAnalysis']['properties']['level_group_name'],'main_performance':{'type':'string','minLength':1},
                          'difficulty_groups':group_schema,'teaching_design':full_schema['$defs']['levelAnalysis']['properties']['teaching_design']}}}}}
    def level_task(level):
        subset = [s for s in students if s['diagnosis']['level'] == level]
        # Use the actual individual diagnoses, without repeating long transcripts.
        brief = [{'student':s['student'], 'diagnosis':{'level':level,'rationale':s['diagnosis']['rationale'], 'evidence':s['diagnosis']['evidence']},
                  'thinking_structure_summary':s.get('thinking_structure_summary',''),
                  'progression':{'main_obstacle':s['progression']['main_obstacle']}} for s in subset]
        inp = {'precision_teaching_context':payload['precision_teaching_context'], 'diagnostic_task_text':payload.get('diagnostic_task_text',''), 'student_diagnosis_results':brief}
        suffix = ('本次只归纳这一个SOLO层级的问题，不生成班级报告、分组或其他层级。'
            '比较同层级学生的主要障碍，优先归纳一个有代表性的主要问题；只有确实需要不同教学支持时才增加第二个，'
            '每层level_group_name必须从teaching_design.task的具体学习任务提炼2至6字的学习活动名称，体现学生要做什么，禁止使用学生问题、SOLO层级名称或通用套话。不得为了覆盖每个人强行分类。每项包含18字以内problem_label、一句话problem_description、'
            '有依据的student_ids及一句teaching_suggestion；group_name应结合当前学科内容和学生要完成的任务，用教师易懂的短语命名，不照搬其他学科示例，不用SOLO术语或层级名；不要输出difficulty字段。'
            'main_performance用2至4句概括具体作答表现及有依据的差异。problem_description必须依据学生实际表达写出本题具体的问题，不得只改写SOLO定义或虚构事实。'
            '区分学生自主提出、经AI追问后提出和仅确认AI的内容，不把后两者都当作自主掌握。'
            'teaching_design依教师提供的SOLO进阶设计依据分别写content（材料内容）、task（学习任务）、scaffold（必要支架），每项一句，具体贴合当前学科任务和障碍。'
            'P先支持一个相关回应；U先扩展另一个相关要素，不强求完整关系链；M按任务需要连接要素，关系不必都是因果；R根据材料和教学目标可提供新情境、条件变化或边界检验。不得把材料中未证明的关系或可选资源写成已知事实，支架不代替学生作答。')
        result = _run_rubric_compatible(skill, inp, level_schema, 'class_level_problems', suffix)
        return level, result['level_analyses'][0]
    def overview_task():
        overview_schema = class_transport_schema(full_schema, compact_large=True, separate_problems=True)
        suffix = ('本次只生成班级整体与层级概览，不输出difficulty_groups或grouping_recommendations，平台会单独生成。'
            '典型证据仅输出student_id和该学生真实source_ref；不要输出原话、姓名或解释。'
            'common_issues的basis_student_ids及individual_attention的student_id必须逐字复制输入中student.student_id，禁止用姓名、序号或示例编号代替。'
            '每项简短，individual_attention只列有明确原话依据的必要提醒；身体不适、参与状态和明确表达的情绪应区分，不得由拒答或催促推断情绪，不作心理诊断。')
        return _run_rubric_compatible(skill, payload, overview_schema, 'class_overview', suffix)
    with ThreadPoolExecutor(max_workers=3) as pool:
        overview_future = pool.submit(overview_task)
        futures = [pool.submit(level_task, level) for level in levels]
        overview = overview_future.result()
        groups_by_level = dict(future.result() for future in futures)
    for row in overview['level_analyses']:
        level_result = groups_by_level[row['level']]
        if level_result.get('level_group_name'):
            row['level_group_name'] = level_result['level_group_name']
        row['difficulty_groups'] = level_result['difficulty_groups']
        row['main_performance'] = level_result['main_performance']
        row['teaching_design'] = level_result['teaching_design']
    _validate_class_problem_groups(overview, payload)
    overview['task_group_names_version'] = '1'
    result = expand_class(expand_large_class_output(overview, payload), payload)
    jsonschema.validate(result, full_schema)
    result['task_group_names_version'] = '1'
    result['problem_grouping_version'] = '1.1'
    return result, None


def run_goal_path_design(
    skill: SkillDefinition, payload: dict[str, Any], provider: str
) -> tuple[dict[str, Any], None]:
    if skill.key != "precision_intervention_goal_path_design":
        raise ValueError("此接口只用于目标与路径设计 Skill")
    if provider == "mock":
        raise RuntimeError("目标与路径设计需要真实模型；Mock AI 不生成正式方案")
    schema = json.loads((skill.path.parent / "assets/goal-path-output.schema.json").read_text(encoding="utf-8"))
    from .activity_scope import without_activity_rosters
    payload = without_activity_rosters(payload)
    previous = payload.get("previous_goal_path_design")
    if previous is not None:
        from .goal_path_revision import REVISION_SCHEMA, REVISION_INSTRUCTIONS, apply_goal_path_revision
        revision = _run_structured_skill(
            skill, payload, "goal-path-output.schema.json", "precision_intervention_goal_path_revision",
            provider, schema_override=REVISION_SCHEMA, instruction_suffix=REVISION_INSTRUCTIONS,
        )
        import jsonschema
        jsonschema.validate(revision, REVISION_SCHEMA)
        result = apply_goal_path_revision(previous, revision)
    else:
        result = _run_structured_skill(
            skill, payload, "goal-path-output.schema.json", "precision_intervention_goal_path",
            provider, instruction_suffix=(
                "\n\n只输出原始目标与路径JSON对象，教师可读报告由平台排版，不另行生成。"
                "遵循已加载的目标、路径和输入输出规则；保留教师本轮要求及未涉及的合理编辑。"
            ),
        )
    try:
        import jsonschema
    except ImportError as error:
        raise RuntimeError("缺少 jsonschema 依赖，无法校验目标与路径方案") from error
    jsonschema.validate(result, schema)
    return result, None


def run_goal_path_teacher_analysis(payload: dict[str, Any], provider: str) -> dict[str, str]:
    """Prepare the two teacher-reviewed inputs required by the goal/path Skill."""
    if provider not in {"deepseek", "openrouter"}:
        raise RuntimeError("教学分析草稿需要 DeepSeek 或 OpenRouter 配置")
    if not settings.ai_api_key or not settings.ai_model:
        key_name = "OPENROUTER_API_KEY" if provider == "openrouter" else "AI_API_KEY"
        raise RuntimeError(f"尚未配置 {key_name} 或 AI_MODEL")
    instructions = (
        "你是教师备课助手。仅根据输入的精准教学资料和班级 SOLO 聚合诊断，生成两项供教师核对的分析草稿。"
        "输出严格的 JSON 对象，且仅有 content_analysis、focus_analysis、source_note 三个非空字符串字段。"
        "content_analysis 分析教学内容及与课程要求的关系；focus_analysis 分析本课教学重点和难点。"
        "不可把班级诊断改写为学生重新定级，不写任何学生姓名，不设计目标、活动或支架。"
        "中小学依据课程标准，高等教育依据课程大纲与课程目标。输入未提供原文时，不得编造课标或课程大纲条款、专业认证指标、教材章节或精确出处；"
        "在 source_note 明确说明哪些课程依据尚需教师核对。若教学主题、学科、年级或内容互相矛盾，也在 source_note 标明。"
    )
    from .education_context import education_runtime_rules
    instructions += "\n\n" + education_runtime_rules()
    body = {
        "model": settings.ai_model,
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 8000,
        "stream": False,
    }
    if provider == "deepseek":
        body.update({"thinking": {"type": "enabled"}, "temperature": 0.1})
    else:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "teacher_analysis",
                "strict": False,
                "schema": {
                    "type": "object",
                    "properties": {key: {"type": "string"} for key in ("content_analysis", "focus_analysis", "source_note")},
                    "required": ["content_analysis", "focus_analysis", "source_note"],
                    "additionalProperties": False,
                },
            },
        }
    last_error: Exception | None = None
    for attempt in range(2):
        if attempt:
            body["max_tokens"] = 12000
            body["messages"][-1]["content"] += "\n请简洁作答，优先确保三个字段构成完整 JSON。"
        response = _post_json(f"{settings.ai_base_url}/chat/completions", body, settings.ai_api_key, purpose="teacher_analysis")
        try:
            result = json.loads(_extract_chat_completion_text(response))
            break
        except (RuntimeError, json.JSONDecodeError) as error:
            last_error = error
    else:
        raise RuntimeError(f"模型未返回完整教学分析 JSON：{last_error}") from last_error
    required = ("content_analysis", "focus_analysis", "source_note")
    if not isinstance(result, dict) or any(not isinstance(result.get(key), str) or not result[key].strip() for key in required):
        raise RuntimeError("教学分析草稿缺少必要字段")
    return {key: result[key].strip() for key in required}


def run_activity_formative_design(
    skill: SkillDefinition, payload: dict[str, Any], provider: str
) -> dict[str, Any]:
    from .activity_scope import without_activity_rosters
    payload = without_activity_rosters(payload)
    if skill.key != "precision_intervention_activity_formative_regulation":
        raise ValueError("此接口只用于学习活动与形成性评价 Skill")
    if provider == "mock":
        raise RuntimeError("学习活动设计需要真实模型；Mock AI 不生成正式方案")
    schema = json.loads((skill.path.parent / "assets/activity-formative-output.schema.json").read_text(encoding="utf-8"))
    parallel_stages = [
        {"stage_id": stage["stage_id"], "unit_ids": [unit["unit_id"] for unit in stage["activity_units"]]}
        for stage in payload["goal_path_design"]["intervention_path"]["primary"]["stages"]
        if len(stage["activity_units"]) > 1
    ]
    instructions = (
        "\n\n本次只输出符合机器 JSON Schema 的 activity_formative_design 对象。"
        "教师端活动表和可读报告由平台根据该对象排版，不要额外重复输出 Markdown 报告。"
        "同阶段并行单元合并为一个顺序活动；形成性评价仅在关键决策点设置，单节课通常 1—2 次。"
        "形成性评价表每格用短句，只保留可观察证据、一个关键判断及可执行的处理；每个目标的达到/未达到标准各用一句简短描述，单条标准或处理尽量不超过40个汉字，不重复目标全文或活动背景。"
        "每个评价节点只覆盖本次决策真正需要判断的目标，不要在每个节点机械列出全部五个层级及两套长标准。"
        "不改变上游阶段、目标、学生归属及总时长。"
        "并行阶段必须在 activity_sequence_summary 中只有一个 activity_id、simultaneous=true；"
        "该活动的 source_unit_ids 包含该阶段全部单元，parallel_group_tasks 与单元一一对应。"
        "活动和分组的 target_goal_ids 必须严格等于各自上游单元的目标编号；"
        "共同核心目标 CG 可以体现在活动目标文字或形成性评价中，但不要作为活动单元的额外目标编号。"
        f"并行阶段清单：{json.dumps(parallel_stages, ensure_ascii=False)}"
    )
    result = _run_structured_skill(
        skill, payload, "activity-formative-output.schema.json", "activity_formative_design",
        provider, instruction_suffix=instructions,
    )
    try:
        import jsonschema
    except ImportError as error:
        raise RuntimeError("缺少 jsonschema 依赖，无法校验学习活动方案") from error
    jsonschema.validate(result, schema)
    return result


def run_intervention_plan_integration(
    skill: SkillDefinition, payload: dict[str, Any], provider: str
) -> dict[str, Any]:
    if skill.key != "precision_intervention_plan_integration_review":
        raise ValueError("此接口只用于精准干预方案整合 Skill")
    if provider == "mock":
        raise RuntimeError("完整方案整合与审核需要真实模型；Mock AI 不生成正式报告")
    instructions = (
        "\n\n只输出符合 plan-integration-output.schema.json 的 JSON 对象。"
        "必须逐项审查 R1—R8；保留上游目标、活动、时长、学生归属和形成性评价，不得静默改写。"
        "每个 review_items 项都必须包含 rule_code、check_item、status、issue、suggestion、return_to 六个字段；通过项后三项为‘—’，非通过项须给出具体问题、建议及返回位置。"
        "basic_information中的精准教学信息逐值复制precision_teaching_context；高等教育的subject允许为空，不能补写学科、课程名或未填写说明，课程名称和专业使用各自字段。"
        "basic_information.plan_title、共同核心目标 goal_statement、每条分层目标的 goal_id/goal_statement、"
        "五层分布及路径阶段的 stage_id/duration_minutes 必须逐字或逐值复制上游输入。"
        "teaching_context_and_conditions 中的两项教师确认分析，以及教学时长、教学环境与AI支持条件、课堂构想，"
        "必须原样复制 teacher_instructional_context，不得压缩或改写。"
        "若发现已确认的上游内容或背景互相矛盾，在审核表中指出并标记返回位置；不要自行修正。"
        "默认隐藏学生姓名。教师版不展示内部追溯编号、总体教学路径或课堂教学设计构想。"
        "不要额外输出 Markdown、代码围栏或干预结束后的成效评价。"
    )
    result = _run_structured_skill(
        skill, payload, "plan-integration-output.schema.json", "precision_intervention_plan",
        provider, instruction_suffix=instructions,
    )
    import jsonschema
    schema = json.loads((skill.path.parent / "assets/plan-integration-output.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(result, schema)
    return result


def run_skill(skill: SkillDefinition, payload: dict[str, Any], provider: str) -> dict[str, Any]:
    if skill.key == "expression_polish":
        if provider != "mock":
            raise RuntimeError(f"当前表述优化尚未配置 Provider：{provider}")
        return _polish_expression(payload)
    if skill.key == "intervention_design":
        if provider != "mock":
            raise RuntimeError(f"当前干预设计尚未配置 Provider：{provider}")
        return _design_intervention(payload)
    if skill.key == "solo_task_rubric_builder":
        from .compact_outputs import RUBRIC_SCHEMA, expand_rubric
        intent = payload.get('diagnostic_task_design_intent')
        if not isinstance(intent, str) or not intent.strip():
            raise ValueError('生成 SOLO 分析标准前请补充诊断任务设计意图。')
        result = _mock_task_rubric(payload) if provider == "mock" else _run_structured_skill(
            skill, payload, "task-specific-solo-rubric.schema.json", "solo_task_rubric", provider,
            schema_override=RUBRIC_SCHEMA,
            instruction_suffix=(
                "\n教师界面将 core_performance 显示为主要认知特征，decision_evidence 显示为学生表现，"
                "typical_expressions 显示为示例。typical_expressions 中只写符合当前具体任务的假想学生表达，"
                "先依据内置通用SOLO标准确定边界，任务分析只保留必要内容与关系，不强制枚举要素和路径。"
                "只输出五级levels的三栏和必要notes；不得输出任务拆解、编号、追溯字段或来源版本信息。"
                "使用学生第一人称的实际句子，不写层级定义、判定提醒、边界说明或真实学生诊断；"
                "相邻层级边界融入学生表现，任务观察限制只写notes。"
                "五级 status 均为 defined；每级 core_performance、decision_evidence、typical_expressions 必须有具体内容，任何一栏不得留空。"
                "即使任务未主动要求概括或迁移，也要定义学生自主展示该结构时的标准及假想示例，不增加原题隐藏要求。"
                "不得在表格标准中使用‘当前任务难以引出’或以任务未要求替代学生表现；观察机会限制只写备注，不能作为学生定级上限。"
            ),
        )
        result = expand_rubric(result)
        validate_task_rubric(skill, result, require_complete=True)
        return result
    if skill.key == "solo_student_diagnosis_feedback":
        result, _, _ = run_student_diagnosis_report(skill, payload, provider)
        return result
    raise KeyError(f"未实现 Skill：{skill.key}")
