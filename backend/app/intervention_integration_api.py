from __future__ import annotations

import copy
import hashlib
import json
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from .activity_formative_api import _serialized as activity_serialized
from .config import settings
from .database import database, fetch_one, utc_now, write_audit
from .goal_path_api import _aliases, _replace_names, _serialized_row as goal_serialized
from .services.ai import run_intervention_plan_integration
from .services.report_pdf import render_report_pdf
from .services.report_docx import render_report_docx
from .services.skills import load_skill


router = APIRouter(prefix="/api")
RULE_NAMES = [
    "诊断—目标一致性", "目标—活动一致性", "路径—活动一致性", "差异化合理性",
    "支架适切性", "活动—形成性评价一致性", "师生机角色合理性", "课堂可实施性",
]
TEACHER_ANALYSIS_FIELDS = (
    "teaching_content_and_curriculum_analysis",
    "teaching_focus_and_difficulty_analysis",
)


def _replace_student_ids(value: object, aliases: dict[str, str]) -> object:
    if isinstance(value, dict):
        return {key: _replace_student_ids(item, aliases) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_student_ids(item, aliases) for item in value]
    if isinstance(value, str):
        for student_id, alias in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
            value = value.replace(student_id, alias)
        return value
    return value


def _apply_teacher_overrides(result: dict, overrides: dict) -> dict:
    merged = copy.deepcopy(result)
    for activity in merged["activities"]:
        fields = overrides.get(activity["activity_id"], {})
        if fields.get("objective_full") or fields.get("objective"):
            activity["activity_objective"] = fields.get("objective_full") or fields["objective"]
        if fields.get("product_full") or fields.get("product"):
            activity["learning_product"] = fields.get("product_full") or fields["product"]
        if fields.get("student_full") or fields.get("student"):
            student_actions = [item for item in activity["participant_actions"] if item["actor"] in {"学生", "同伴"}]
            if student_actions:
                student_actions[0]["action"] = fields.get("student_full") or fields["student"]
                activity["participant_actions"] = [
                    item for item in activity["participant_actions"]
                    if item is student_actions[0] or item["actor"] not in {"学生", "同伴"}
                ]
    return merged


def _restore_confirmed_teacher_analysis(source: dict, result: dict) -> dict:
    """Replace model paraphrases with the teacher's exact confirmed analyses.

    Duration, environment/AI conditions, and lesson conception are never
    rewritten: a change to those fields remains a validation error. An
    omitted optional null is restored solely for exact source comparison.
    """
    corrected = copy.deepcopy(result)
    source_context = source["teacher_instructional_context"]
    output_context = corrected["integrated_plan"]["teaching_context_and_conditions"]
    changed_conditions = [
        key for key, value in source_context.items()
        if key not in TEACHER_ANALYSIS_FIELDS and output_context.get(key) != value
    ]
    if changed_conditions:
        raise ValueError("报告改写了教师确认的教学条件：" + "、".join(changed_conditions))
    for key, value in source_context.items():
        if key not in output_context and value is None:
            output_context[key] = None
    for key in TEACHER_ANALYSIS_FIELDS:
        output_context[key] = source_context[key]
    return corrected


def _source(teaching_id: str, classroom_id: str) -> tuple[dict, str, str]:
    skill = load_skill("precision_intervention_plan_integration_review")
    class_row = fetch_one(
        "SELECT * FROM class_diagnosis_reports WHERE teaching_id = ? AND classroom_id = ?",
        (teaching_id, classroom_id),
    )
    goal = goal_serialized(teaching_id, classroom_id)
    activity = activity_serialized(teaching_id, classroom_id)
    teaching = fetch_one("SELECT * FROM precision_teachings WHERE id = ?", (teaching_id,))
    task = fetch_one("SELECT * FROM diagnosis_tasks WHERE teaching_id = ?", (teaching_id,))
    classroom = fetch_one("SELECT * FROM classrooms WHERE id = ?", (classroom_id,))
    teacher = fetch_one("SELECT * FROM teacher_workspaces WHERE id = ?", (teaching["workspace_id"],)) if teaching else None
    if not all((class_row, goal, activity, teaching, task, classroom, teacher)):
        raise HTTPException(status_code=409, detail="请先完成班级诊断、目标路径和学习活动设计")
    if class_row["status"] != "ready" or goal["status"] != "teacher_confirmed" or activity["status"] != "teacher_confirmed":
        raise HTTPException(status_code=409, detail="请先确认当前班级的诊断、目标路径和学习活动")
    if not task["task_text"].strip():
        raise HTTPException(status_code=409, detail="诊断任务原文为空，无法整合报告")
    class_result = json.loads(class_row["generated_json"])
    if class_result.get("schema_version") not in {"1.0", "1.1"}:
        raise HTTPException(status_code=409, detail="班级诊断版本与报告 Skill 不兼容")
    if goal["result"].get("schema_version") != "1.2" or activity["result"].get("schema_version") != "1.3":
        raise HTTPException(status_code=409, detail="目标路径或学习活动版本与报告 Skill 不兼容")
    aliases = _aliases(class_result)
    student_ids = [student["student_id"] for row in class_result["class_summary"]["distribution"] for student in row["students"]]
    id_aliases = {student_id: f"student-{index}" for index, student_id in enumerate(student_ids, 1)}
    contents = [line.strip() for line in (teaching["content"] or "").splitlines() if line.strip()]
    teacher_context = goal["teacher_instructional_context"]
    source_activity = _apply_teacher_overrides(activity["result"], activity.get("presentation_overrides") or {})
    payload = {
        "schema_version": "1.2",
        "precision_teaching_context": {
            "subject": teaching["subject"],
            "grade": teaching["grade"],
            "textbook_version_and_chapter": teaching["textbook"] or "未填写教材章节",
            "precision_teaching_topic": teaching["title"],
            "precision_teaching_goals": [teaching["goal"]],
            "precision_teaching_content": contents or [teaching["title"]],
        },
        "teacher_instructional_context": teacher_context,
        "diagnostic_task_context": {
            "task_id": teaching_id,
            "task_title": None,
            "task_text": task["task_text"],
            "task_materials_summary": None,
            "response_mode": "AI 对话与最终文字成果",
            "task_requirements": "依据诊断任务原文与 AI 对话，并提交最终文字成果。",
            "source_ref": None,
        },
        "class_diagnosis_result": _replace_student_ids(_replace_names(class_result, aliases), id_aliases),
        "goal_path_design": _replace_student_ids(_replace_names(goal["result"], aliases), id_aliases),
        "activity_formative_design": _replace_student_ids(_replace_names(source_activity, aliases), id_aliases),
        "integration_preferences": {
            "plan_title": f"{teaching['title']}｜精准干预方案",
            "class_name": classroom["name"],
            "teacher_name": teacher["teacher_name"],
            "teaching_date": None,
            "show_student_names": False,
            "include_review_appendix": True,
            "wording_density": "concise",
        },
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    source_hash = hashlib.sha256((raw + skill.version).encode("utf-8")).hexdigest()
    return payload, source_hash, skill.version


def _validate_output(source: dict, result: dict) -> None:
    expected_versions = {
        "class_diagnosis": source["class_diagnosis_result"]["schema_version"],
        "goal_path_design": source["goal_path_design"]["schema_version"],
        "activity_formative_design": source["activity_formative_design"]["schema_version"],
    }
    if result["source_versions"] != expected_versions:
        raise ValueError("报告未如实标记上游版本")
    plan = result["integrated_plan"]
    basic = plan["basic_information"]
    for key, value in source["precision_teaching_context"].items():
        if basic[key] != value:
            raise ValueError(f"报告改写了精准教学信息：{key}")
    if plan["teaching_context_and_conditions"] != source["teacher_instructional_context"]:
        raise ValueError("报告改写了教师确认的教学条件")
    for key, value in source["diagnostic_task_context"].items():
        if plan["diagnosis_summary"]["diagnostic_task"][key] != value:
            raise ValueError(f"报告改写了诊断任务：{key}")
    if basic["total_duration_minutes"] != source["teacher_instructional_context"]["planned_duration_minutes"]:
        raise ValueError("报告总时长与教师设置不一致")
    if basic["plan_title"] != source["integration_preferences"]["plan_title"]:
        raise ValueError("报告标题与教师设置不一致")
    source_distribution = source["class_diagnosis_result"]["class_summary"]["distribution"]
    output_distribution = plan["diagnosis_summary"]["distribution_rows"]
    if [(row["level"], row["count"], row["proportion"]) for row in output_distribution] != [
        (row["level"], row["count"], row["proportion"]) for row in source_distribution
    ]:
        raise ValueError("报告改变了班级 SOLO 层级分布")
    source_goal = source["goal_path_design"]
    if plan["goal_design"]["common_core_goal"]["goal_statement"] != source_goal["common_core_goal"]["goal_statement"]:
        raise ValueError("报告改变了共同核心目标")
    if [(item["goal_id"], item["goal_statement"]) for item in plan["goal_design"]["progression_goals"]] != [
        (item["goal_id"], item["goal_statement"]) for item in source_goal["progression_goals"]
    ]:
        raise ValueError("报告改变了分层进阶目标")
    source_stages = source_goal["intervention_path"]["stages"]
    output_stages = plan["overall_path"]["stages"]
    if [(item["stage_id"], item["duration_minutes"]) for item in output_stages] != [
        (item["stage_id"], item["duration_minutes"]) for item in source_stages
    ]:
        raise ValueError("报告改变了路径阶段或时长")
    source_activities = source["activity_formative_design"]["activities"]
    output_activities = plan["activities"]
    if len(source_activities) != len(output_activities):
        raise ValueError("报告改变了学习活动数量")
    for original, rendered in zip(source_activities, output_activities):
        for key in ("activity_id", "source_stage_id", "source_unit_ids", "duration_minutes", "organization_forms"):
            if rendered[key] != original[key]:
                raise ValueError(f"报告改变了活动映射或时长：{original['activity_id']}")
    if [item["evaluation_id"] for item in plan["formative_evaluations"]] != [item["evaluation_id"] for item in source["activity_formative_design"]["formative_evaluation_nodes"]]:
        raise ValueError("报告改变了形成性评价节点")
    reviews = result["audit_summary"]["review_items"]
    if [(item["rule_code"], item["check_item"]) for item in reviews] != [(f"R{i}", name) for i, name in enumerate(RULE_NAMES, 1)]:
        raise ValueError("审核表必须按 R1—R8 恰好输出八项")
    for item in reviews:
        if item["status"] == "✓ 通过":
            if (item["issue"], item["suggestion"], item["return_to"]) != ("—", "—", "—"):
                raise ValueError("通过项的问题、建议和返回位置应为破折号")
        elif "—" in (item["issue"], item["suggestion"]):
            raise ValueError("建议优化或需要修改的审核项缺少具体问题与建议")
    statuses = {item["status"] for item in reviews}
    expected_status, expected_conclusion = (
        ("needs_revision", "需要修改后使用") if "× 需要修改" in statuses else
        ("ready_with_suggestions", "可使用并建议优化") if "△ 建议优化" in statuses else
        ("ready_for_use", "可直接使用")
    )
    if result["integration_status"] != expected_status or result["audit_summary"]["overall_conclusion"] != expected_conclusion:
        raise ValueError("报告整体结论与八项审核结果不一致")
    if not source["integration_preferences"]["show_student_names"]:
        if any(row["students_display"] is not None for row in plan["diagnosis_summary"]["distribution_rows"]):
            raise ValueError("默认报告不得显示学生姓名")


def _saved(teaching_id: str, classroom_id: str, current_hash: str | None) -> dict | None:
    row = fetch_one(
        "SELECT * FROM intervention_integration_reports WHERE teaching_id = ? AND classroom_id = ?",
        (teaching_id, classroom_id),
    )
    if not row:
        return None
    return {
        "teaching_id": teaching_id, "classroom_id": classroom_id,
        "skill_key": row["skill_key"], "skill_version": row["skill_version"],
        "provider": row["provider"],
        "status": "current" if current_hash == row["source_hash"] else "stale",
        "result": json.loads(row["generated_json"]),
        "generated_at": row["generated_at"], "updated_at": row["updated_at"],
    }


@router.get("/precision-teachings/{teaching_id}/classrooms/{classroom_id}/integration-report")
def get_integration_report(teaching_id: str, classroom_id: str) -> dict:
    try:
        _, source_hash, _ = _source(teaching_id, classroom_id)
        ready, prerequisite = True, None
    except HTTPException as error:
        source_hash = None
        ready, prerequisite = False, error.detail
    return {"ready": ready, "prerequisite": prerequisite, "report": _saved(teaching_id, classroom_id, source_hash)}


@router.get("/precision-teachings/{teaching_id}/classrooms/{classroom_id}/integration-report.pdf")
def download_integration_report_pdf(teaching_id: str, classroom_id: str) -> Response:
    _, source_hash, _ = _source(teaching_id, classroom_id)
    report = _saved(teaching_id, classroom_id, source_hash)
    if report is None:
        raise HTTPException(status_code=404, detail="尚未生成教学方案")
    if report["status"] != "current":
        raise HTTPException(status_code=409, detail="上游资料已变化，请重新生成教学方案后再导出")
    try:
        pdf = render_report_pdf(report["result"])
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    title = report["result"]["integrated_plan"]["basic_information"]["plan_title"]
    filename = quote(f"{title}-教学方案.pdf", safe="")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=\"teaching-plan.pdf\"; filename*=UTF-8''{filename}",
            "Cache-Control": "no-store",
        },
    )


@router.get("/precision-teachings/{teaching_id}/classrooms/{classroom_id}/integration-report.docx")
def download_integration_report_docx(teaching_id: str, classroom_id: str) -> Response:
    _, source_hash, _ = _source(teaching_id, classroom_id)
    report = _saved(teaching_id, classroom_id, source_hash)
    if report is None:
        raise HTTPException(status_code=404, detail="尚未生成教学方案")
    if report["status"] != "current":
        raise HTTPException(status_code=409, detail="上游资料已变化，请重新生成教学方案后再导出")
    try:
        docx = render_report_docx(report["result"])
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    title = report["result"]["integrated_plan"]["basic_information"]["plan_title"]
    filename = quote(f"{title}-教学方案.docx", safe="")
    return Response(
        content=docx,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f"attachment; filename=\"teaching-plan.docx\"; filename*=UTF-8''{filename}",
            "Cache-Control": "no-store",
        },
    )


@router.post("/precision-teachings/{teaching_id}/classrooms/{classroom_id}/integration-report/generate")
def generate_integration_report(teaching_id: str, classroom_id: str) -> dict:
    skill = load_skill("precision_intervention_plan_integration_review")
    payload, source_hash, _ = _source(teaching_id, classroom_id)
    try:
        import jsonschema
        schema = json.loads((skill.path.parent / "assets/plan-integration-input.schema.json").read_text(encoding="utf-8"))
        jsonschema.validate(payload, schema)
    except Exception as error:
        raise HTTPException(status_code=422, detail=f"教学方案输入校验失败：{error}") from error
    job_id = f"job-{uuid4().hex}"
    with database() as connection:
        connection.execute(
            "INSERT INTO ai_jobs (id, skill_key, skill_version, provider, status, input_json, created_at) VALUES (?, ?, ?, ?, 'running', ?, ?)",
            (job_id, skill.key, skill.version, settings.ai_provider, json.dumps(payload, ensure_ascii=False), utc_now()),
        )
    result = None
    try:
        result = run_intervention_plan_integration(skill, payload, settings.ai_provider)
        result = _restore_confirmed_teacher_analysis(payload, result)
        _validate_output(payload, result)
        now = utc_now()
        output_json = json.dumps(result, ensure_ascii=False)
        with database() as connection:
            connection.execute(
                "UPDATE ai_jobs SET status = 'completed', output_json = ?, completed_at = ? WHERE id = ?",
                (output_json, now, job_id),
            )
            connection.execute(
                """INSERT INTO intervention_integration_reports
                (teaching_id, classroom_id, skill_key, skill_version, provider, source_hash, generated_json, generated_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(teaching_id, classroom_id) DO UPDATE SET
                    skill_key = excluded.skill_key, skill_version = excluded.skill_version,
                    provider = excluded.provider, source_hash = excluded.source_hash,
                    generated_json = excluded.generated_json, generated_at = excluded.generated_at,
                    updated_at = excluded.updated_at""",
                (teaching_id, classroom_id, skill.key, skill.version, settings.ai_provider, source_hash, output_json, now, now),
            )
        write_audit("generate", "intervention_integration_report", f"{teaching_id}:{classroom_id}", {"job_id": job_id})
        return {"report": _saved(teaching_id, classroom_id, source_hash)}
    except Exception as error:
        with database() as connection:
            connection.execute(
                "UPDATE ai_jobs SET status = 'failed', output_json = ?, error = ?, completed_at = ? WHERE id = ?",
                (json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else None, str(error), utc_now(), job_id),
            )
        raise HTTPException(status_code=502, detail=f"教学方案生成失败：{error}") from error
