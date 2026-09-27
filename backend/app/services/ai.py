from __future__ import annotations

import json
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
        "U": ("能够围绕一个相关信息单元形成有效回应。", "自主识别并处理 E1、E2 或 E3 中的一个要素，但尚未扩展到其他要素。", ["G-U", "E1"]),
        "M": ("能够处理多个相关信息单元，但主要是并列或累加。", "涉及两个或更多 E，但尚未建立 R1、R2 所要求的关键组织关系。", ["G-M", "E1", "E2", "E3"]),
        "R": ("能够建立关键关系并形成完整的任务内解释。", "通过 R1、R2 组织多个 E，使条件、依据和结论共同完成当前任务。", ["G-R", "E1", "E2", "E3", "R1", "R2"]),
        "EA": ("能够在完整解释基础上进行概括、迁移或边界检验。", "先形成 R 层整体结构，再把该关系迁移到新情境或讨论其适用条件。", ["G-EA", "R2", "A1"]),
    }
    levels: dict[str, Any] = {}
    for level, (performance, evidence, trace) in level_data.items():
        levels[level] = {
            "status": "defined",
            "core_performance": [performance],
            "decision_evidence": [evidence],
            "adjacent_boundaries": [f"依据任务特定结构判断 {level} 与相邻层级的差异，不按字数或关键词数量定级。"],
            "typical_expressions": ["示例仅用于理解结构，不能作为关键词匹配表。"],
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
        f"- {item['ref']}：\u201c{item['quote']}\u201d\n  {item['interpretation']}"
        for item in diagnosis["evidence"]
    )
    strategies = "\n".join(
        f"{index}. {item['title']}：{item['action']}（适用于：{item['transfer_use']}）"
        for index, item in enumerate(result["strategies"], start=1)
    )
    target_names = {"U": "单点结构", "M": "多点结构", "R": "关联结构", "EA": "拓展抽象结构", "higher_EA": "更高质量的拓展抽象结构"}
    return (
        f"一、当前诊断\n{diagnosis['level']}（{diagnosis['level_name']}）：{diagnosis['rationale']}\n\n"
        f"二、学生证据\n{evidence}\n\n"
        f"三、当前思维结构\n{result['thinking_structure_summary']}\n\n"
        f"四、尚未达到下一层级的原因\n{diagnosis['next_level_gap']}\n\n"
        f"五、Plus One 进阶目标\n{progression['target_level']}（{target_names[progression['target_level']]}）：{progression['concrete_goal']}\n\n"
        f"六、后续学习策略\n{strategies}"
    )


def _skill_runtime_instructions(skill: SkillDefinition) -> str:
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
    sections = [skill.instructions]
    for relative_path in resource_paths:
        path = skill.path.parent / relative_path
        if path.is_file():
            sections.append(f"\n\n# 运行时参考：{relative_path}\n{path.read_text(encoding='utf-8')}")
    output_contract = {
        "precision_diagnostic_task_design": "diagnostic_task_design",
        "solo_student_diagnosis_feedback": "solo_student_diagnosis",
        "solo_class_diagnosis_intervention": "solo_class_diagnosis",
        "precision_intervention_goal_path_design": "precision_intervention_goal_path",
        "precision_intervention_activity_formative_regulation": "activity_formative_design",
        "precision_intervention_plan_integration_review": "precision_intervention_plan",
    }.get(skill.key, "solo_task_rubric")
    sections.append(f"\n\n只输出符合 {output_contract} JSON Schema 的 JSON 对象，不输出 Markdown、代码围栏或额外说明。")
    return "".join(sections)


def run_diagnostic_task_design(skill: SkillDefinition, payload: dict[str, Any], provider: str) -> dict[str, Any]:
    if provider == "mock":
        raise RuntimeError("诊断任务设计需要配置真实模型，当前仍为 Mock AI")
    result = _run_structured_skill(
        skill, payload, "diagnostic-task-output.schema.json", "diagnostic_task_design", provider,
        instruction_suffix="三个候选方案必须有实质差异；预计时长仅表示任务规模，不控制会话结束。",
    )
    import jsonschema
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


def _parse_structured_json(text: str, schema: dict[str, Any]) -> dict[str, Any]:
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
    raise ValueError(f"模型没有返回符合 Schema 的完整 JSON 对象：{last_error}")


def _post_json(url: str, body: dict[str, Any], api_key: str, timeout: int = 120, purpose: str = "teacher_ai") -> dict[str, Any]:
    record_llm_request("teacher", purpose, settings.ai_provider, url, body)
    request = Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout, context=verified_ssl_context()) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"模型请求失败（{error.code}）：{detail}") from error
    except URLError as error:
        raise RuntimeError(f"无法连接模型服务：{error.reason}") from error


def _run_deepseek_structured_skill(
    skill: SkillDefinition,
    payload: dict[str, Any],
    schema: dict[str, Any],
    output_name: str,
    instruction_suffix: str = "",
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
        # 教师端复杂 Skill 保留模型思考能力；reasoning_content 与最终 JSON
        # 共用输出预算，因此给出更充足的上限，并在异常截断时重试。
        "thinking": {"type": "enabled"},
        "max_tokens": 16000,
        "temperature": 0.1,
        "stream": False,
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
    raise RuntimeError(f"DeepSeek 连续两次未返回完整结构化 JSON：{last_error}") from last_error


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
        raise RuntimeError("真实模型尚未配置 AI_API_KEY 或 AI_MODEL")
    schema_path = skill.path.parent / "assets" / schema_filename
    schema = schema_override or json.loads(schema_path.read_text(encoding="utf-8"))
    if provider == "deepseek":
        return _run_deepseek_structured_skill(skill, payload, schema, output_name, instruction_suffix)
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


def validate_task_rubric(skill: SkillDefinition, rubric: dict[str, Any]) -> None:
    schema_path = skill.path.parent / "assets/task-specific-solo-rubric.schema.json"
    try:
        import jsonschema
    except ImportError as error:
        raise RuntimeError("缺少 jsonschema 依赖，无法校验任务量规") from error
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(rubric, schema)


def run_student_diagnosis_report(
    skill: SkillDefinition, payload: dict[str, Any], provider: str
) -> tuple[dict[str, Any], str]:
    if skill.key != "solo_student_diagnosis_feedback":
        raise ValueError("此接口只用于学生个体诊断 Skill")
    schema = json.loads((skill.path.parent / "assets/diagnosis-output.schema.json").read_text(encoding="utf-8"))
    if provider == "mock":
        result = _mock_student_diagnosis(payload)
        report_text = student_diagnosis_report_text(result)
    else:
        envelope_schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["solo_student_diagnosis", "teacher_report_markdown"],
            "properties": {
                "solo_student_diagnosis": schema,
                "teacher_report_markdown": {"type": "string", "minLength": 200},
            },
        }
        template = (skill.path.parent / "assets/diagnosis-output-template.md").read_text(encoding="utf-8")
        instructions = (
            "\n\n此平台以一个 JSON 对象接收 Skill 的两份同步输出："
            "solo_student_diagnosis 是原始机器可读结果，teacher_report_markdown 是教师可读 Markdown。"
            "请在同一次分析中生成两者，保持层级、证据、障碍和策略一致。"
            "teacher_report_markdown 严格按下面模板的六个部分填写，包含文字思维结构图和四维分析；"
            "不要在该字符串末尾附加机器可读 JSON 代码块。"
            f"\n\n{template}"
        )
        envelope = _run_structured_skill(
            skill, payload, "diagnosis-output.schema.json", "solo_student_diagnosis_report",
            provider, schema_override=envelope_schema, instruction_suffix=instructions,
        )
        result = envelope["solo_student_diagnosis"]
        report_text = envelope["teacher_report_markdown"].strip()
        required_sections = (
            "学生信息", "SOLO 诊断结果与依据", "当前思维结构特征",
            "主要问题与进阶障碍", "进阶目标", "后续学习策略",
        )
        if not all(section in report_text for section in required_sections) or not all(
            symbol in report_text for symbol in ("●", "◐", "○")
        ):
            raise RuntimeError("教师报告缺少新版 Skill 要求的六部分或思维结构图")
    try:
        import jsonschema
    except ImportError as error:
        raise RuntimeError("缺少 jsonschema 依赖，无法校验学生诊断报告") from error
    jsonschema.validate(result, schema)
    return result, report_text


def run_class_diagnosis_report(
    skill: SkillDefinition, payload: dict[str, Any], provider: str
) -> tuple[dict[str, Any], str]:
    if skill.key != "solo_class_diagnosis_intervention":
        raise ValueError("此接口只用于班级诊断 Skill")
    if provider == "mock":
        raise RuntimeError("班级诊断需要真实模型；Mock AI 不生成正式班级报告")
    schema = json.loads((skill.path.parent / "assets/class-diagnosis-output.schema.json").read_text(encoding="utf-8"))
    envelope_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["solo_class_diagnosis", "teacher_report_markdown"],
        "properties": {
            "solo_class_diagnosis": schema,
            "teacher_report_markdown": {"type": "string", "minLength": 100},
        },
    }
    template = (skill.path.parent / "assets/class-diagnosis-output-template.md").read_text(encoding="utf-8")
    instructions = (
        "\n\n平台需要在同一次分析中输出 solo_class_diagnosis 和 teacher_report_markdown 两个字段。"
        "前者严格遵守原始 JSON Schema；后者是按模板撰写的教师可读 Markdown，"
        "不得在 Markdown 字符串末尾附加 JSON 代码块。"
        "只能引用输入中的个体诊断，不得重新定级，也不要设计具体课堂活动。"
        f"\n\n{template}"
    )
    envelope = _run_structured_skill(
        skill, payload, "class-diagnosis-output.schema.json", "solo_class_diagnosis_report",
        provider, schema_override=envelope_schema, instruction_suffix=instructions,
    )
    result = envelope["solo_class_diagnosis"]
    report_text = envelope["teacher_report_markdown"].strip()
    try:
        import jsonschema
    except ImportError as error:
        raise RuntimeError("缺少 jsonschema 依赖，无法校验班级诊断报告") from error
    jsonschema.validate(result, schema)
    return result, report_text


def run_goal_path_design(
    skill: SkillDefinition, payload: dict[str, Any], provider: str
) -> tuple[dict[str, Any], str]:
    if skill.key != "precision_intervention_goal_path_design":
        raise ValueError("此接口只用于目标与路径设计 Skill")
    if provider == "mock":
        raise RuntimeError("目标与路径设计需要真实模型；Mock AI 不生成正式方案")
    schema = json.loads((skill.path.parent / "assets/goal-path-output.schema.json").read_text(encoding="utf-8"))
    envelope_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["precision_intervention_goal_path", "teacher_report_markdown"],
        "properties": {
            "precision_intervention_goal_path": schema,
            "teacher_report_markdown": {"type": "string", "minLength": 100},
        },
    }
    template = (skill.path.parent / "assets/goal-path-output-template.md").read_text(encoding="utf-8")
    instructions = (
        "\n\n平台在同一次分析中接收两份同步输出：precision_intervention_goal_path 为原始机器可读结果，"
        "teacher_report_markdown 为按模板撰写的教师可读 Markdown。"
        "严格保持学生上游层级、唯一目标归属和阶段时长；不要生成具体活动步骤、支架或形成性评价。"
        "teacher_report_markdown 不附加 JSON 代码块。"
        f"\n\n{template}"
    )
    envelope = _run_structured_skill(
        skill, payload, "goal-path-output.schema.json", "precision_intervention_goal_path_report",
        provider, schema_override=envelope_schema, instruction_suffix=instructions,
    )
    result = envelope["precision_intervention_goal_path"]
    report_text = envelope["teacher_report_markdown"].strip()
    try:
        import jsonschema
    except ImportError as error:
        raise RuntimeError("缺少 jsonschema 依赖，无法校验目标与路径方案") from error
    jsonschema.validate(result, schema)
    return result, report_text


def run_goal_path_teacher_analysis(payload: dict[str, Any], provider: str) -> dict[str, str]:
    """Prepare the two teacher-reviewed inputs required by the goal/path Skill."""
    if provider != "deepseek":
        raise RuntimeError("教学分析草稿目前需要 DeepSeek 配置")
    if not settings.ai_api_key or not settings.ai_model:
        raise RuntimeError("尚未配置 AI_API_KEY 或 AI_MODEL")
    instructions = (
        "你是教师备课助手。仅根据输入的精准教学资料和班级 SOLO 聚合诊断，生成两项供教师核对的分析草稿。"
        "输出严格的 JSON 对象，且仅有 content_analysis、focus_analysis、source_note 三个非空字符串字段。"
        "content_analysis 分析教学内容及与课程要求的关系；focus_analysis 分析本课教学重点和难点。"
        "不可把班级诊断改写为学生重新定级，不写任何学生姓名，不设计目标、活动或支架。"
        "输入未提供课程标准原文时，不得编造课标条款、教材章节或精确出处；"
        "在 source_note 明确说明哪些课程依据尚需教师核对。若教学主题、学科、年级或内容互相矛盾，也在 source_note 标明。"
    )
    body = {
        "model": settings.ai_model,
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
        "thinking": {"type": "enabled"},
        "max_tokens": 8000,
        "temperature": 0.1,
        "stream": False,
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
        result = _mock_task_rubric(payload) if provider == "mock" else _run_structured_skill(
            skill, payload, "task-specific-solo-rubric.schema.json", "solo_task_rubric", provider
        )
        validate_task_rubric(skill, result)
        return result
    if skill.key == "solo_student_diagnosis_feedback":
        result, _ = run_student_diagnosis_report(skill, payload, provider)
        return result
    raise KeyError(f"未实现 Skill：{skill.key}")
