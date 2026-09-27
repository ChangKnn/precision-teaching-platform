from __future__ import annotations

import copy
import hashlib
import json
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .config import settings
from .database import database, fetch_all, fetch_one, utc_now, write_audit
from .services.ai import run_goal_path_design, run_goal_path_teacher_analysis
from .services.skills import load_skill


router = APIRouter(prefix="/api")


class TeacherInstructionalContext(BaseModel):
    teaching_content_and_curriculum_analysis: str = Field(min_length=1, max_length=10000)
    teaching_focus_and_difficulty_analysis: str = Field(min_length=1, max_length=10000)
    planned_duration_minutes: int = Field(ge=10, le=300)
    teaching_environment_and_ai_support_conditions: str = Field(min_length=1, max_length=10000)
    teacher_lesson_conception: str | None = Field(default=None, max_length=10000)


class GoalPathGenerateRequest(BaseModel):
    teacher_instructional_context: TeacherInstructionalContext


class GoalPathSaveRequest(BaseModel):
    result: dict
    confirmed: bool = False


class TeacherAnalysisSaveRequest(BaseModel):
    content_analysis: str = Field(min_length=1, max_length=10000)
    focus_analysis: str = Field(min_length=1, max_length=10000)


def _source_report(teaching_id: str, classroom_id: str) -> dict:
    assignment = fetch_one(
        "SELECT 1 FROM diagnosis_assignments WHERE teaching_id = ? AND classroom_id = ? AND is_published = 1",
        (teaching_id, classroom_id),
    )
    if not assignment:
        raise HTTPException(status_code=404, detail="该精准教学未发布到所选班级")
    report = fetch_one(
        "SELECT * FROM class_diagnosis_reports WHERE teaching_id = ? AND classroom_id = ?",
        (teaching_id, classroom_id),
    )
    if not report or report["status"] != "ready" or not report["generated_json"]:
        raise HTTPException(status_code=409, detail="请先生成当前班级的 SOLO 聚合诊断报告")
    return report


def _student_levels(class_result: dict) -> dict[str, str]:
    assignments = class_result["traceability"]["level_assignments"]
    levels = {item["student_id"]: item["original_level"] for item in assignments}
    if not levels or len(levels) != len(assignments) or class_result["traceability"]["levels_unchanged"] is not True:
        raise ValueError("班级诊断的学生层级追溯不完整")
    distribution = class_result["class_summary"]["distribution"]
    listed = [student["student_id"] for item in distribution for student in item["students"]]
    if len(listed) != len(levels) or set(listed) != set(levels) or class_result["class_summary"]["total_students"] != len(levels):
        raise ValueError("班级诊断人数、名单与层级追溯不一致")
    for item in distribution:
        if item["count"] != len(item["students"]):
            raise ValueError("班级诊断层级人数与名单不一致")
        if any(levels[student["student_id"]] != item["level"] for student in item["students"]):
            raise ValueError("班级诊断中同一学生出现多个当前层级")
    return levels


def _aliases(class_result: dict) -> dict[str, str]:
    students = [
        student for item in class_result["class_summary"]["distribution"]
        for student in item["students"]
    ]
    return {item["student_name"]: f"匿名学生-{index:03d}" for index, item in enumerate(students, 1)}


def _replace_names(value, names: dict[str, str]):
    if isinstance(value, str):
        for original, alias in sorted(names.items(), key=lambda item: len(item[0]), reverse=True):
            value = value.replace(original, alias)
        return value
    if isinstance(value, list):
        return [_replace_names(item, names) for item in value]
    if isinstance(value, dict):
        return {key: _replace_names(item, names) for key, item in value.items()}
    return value


def _build_input(teaching_id: str, classroom_id: str, teacher_context: dict) -> tuple[dict, dict, dict]:
    report = _source_report(teaching_id, classroom_id)
    teaching = fetch_one("SELECT * FROM precision_teachings WHERE id = ?", (teaching_id,))
    class_result = json.loads(report["generated_json"])
    _student_levels(class_result)
    contents = [line.strip() for line in teaching["content"].splitlines() if line.strip()]
    names = _aliases(class_result)
    payload = {
        "schema_version": "1.1",
        "precision_teaching_context": {
            "subject": teaching["subject"],
            "grade": teaching["grade"],
            "textbook_version_and_chapter": teaching["textbook"] or "未填写教材章节",
            "precision_teaching_topic": teaching["title"],
            "precision_teaching_goals": [teaching["goal"]],
            "precision_teaching_content": contents or [teaching["title"]],
        },
        "diagnosis_bundle": {
            "class_diagnosis": _replace_names(class_result, names),
            "source_versions": {
                "class_diagnosis": report["skill_version"],
            },
        },
        "teacher_instructional_context": teacher_context,
    }
    return payload, class_result, report


def _validate_design(result: dict, class_result: dict, planned_minutes: int) -> None:
    skill = load_skill("precision_intervention_goal_path_design")
    try:
        import jsonschema
    except ImportError as error:
        raise RuntimeError("缺少 jsonschema 依赖，无法校验目标与路径方案") from error
    schema = json.loads((skill.path.parent / "assets/goal-path-output.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(result, schema)
    levels = _student_levels(class_result)
    names = {
        student["student_id"]: student["student_name"]
        for item in class_result["class_summary"]["distribution"]
        for student in item["students"]
    }
    def check_student(student: dict) -> None:
        if names.get(student["student_id"]) != student["student_name"]:
            raise ValueError("方案中的学生姓名与班级诊断不一致")
    assignments = result["student_goal_assignments"]
    assignment_ids = [item["student_id"] for item in assignments]
    if len(assignment_ids) != len(levels) or set(assignment_ids) != set(levels):
        raise ValueError("每名学生必须恰好归属一个主要进阶目标")
    goals = {item["goal_id"]: item for item in result["progression_goals"]}
    if len(goals) != len(result["progression_goals"]):
        raise ValueError("进阶目标编号重复")
    for assignment in assignments:
        check_student(assignment)
        if assignment["original_level"] != levels[assignment["student_id"]]:
            raise ValueError("方案修改了学生原始 SOLO 层级")
        goal = goals.get(assignment["primary_goal_id"])
        if not goal or assignment["student_id"] not in {item["student_id"] for item in goal["target_students"]}:
            raise ValueError("学生目标归属与进阶目标名单不一致")
    goal_members = [item["student_id"] for goal in goals.values() for item in goal["target_students"]]
    if len(goal_members) != len(levels) or set(goal_members) != set(levels):
        raise ValueError("进阶目标的学生名单有重复或遗漏")
    for goal in goals.values():
        for student in goal["target_students"]:
            check_student(student)
            if student["current_level"] != levels[student["student_id"]]:
                raise ValueError("进阶目标修改了学生当前层级")
    path = result["intervention_path"]
    stages = path["stages"]
    if sum(stage["duration_minutes"] for stage in stages) != planned_minutes or path["total_duration_minutes"] != planned_minutes:
        raise ValueError("课堂阶段时长总和必须等于教师设定的总时长")
    stage_ids = [stage["stage_id"] for stage in stages]
    if stage_ids != [f"ST{index}" for index in range(1, len(stages) + 1)]:
        raise ValueError("课堂阶段编号必须连续")
    unit_ids = []
    for stage in stages:
        for unit in stage["activity_units"]:
            unit_ids.append(unit["unit_id"])
            if not unit["unit_id"].startswith(f"{stage['stage_id']}-"):
                raise ValueError("并行活动单元必须共享所属阶段编号")
            if not set(unit["target_goal_ids"]) <= set(goals):
                raise ValueError("课堂活动引用了不存在的进阶目标")
            if not {student["student_id"] for student in unit["target_students"]} <= set(levels):
                raise ValueError("课堂活动引用了不存在的学生")
            for student in unit["target_students"]:
                check_student(student)
    if len(unit_ids) != len(set(unit_ids)):
        raise ValueError("课堂活动单元编号重复")


def _markdown(result: dict) -> str:
    common = result["common_core_goal"]
    path = result["intervention_path"]
    student_names = lambda students: "、".join(item["student_name"] for item in students)
    lines = [
        "# 精准干预目标与课堂路径设计", "",
        "## 1. 共同核心目标", "",
        f"**核心目标：** {common['goal_statement']}", "",
        f"- **目标认知结构及理由：** {common['target_cognitive_structure']['solo_level']}（{common['target_cognitive_structure']['level_name']}）——{common['target_cognitive_structure']['structure_and_rationale']}",
        f"- **可观察的达成表现：** {'；'.join(common['observable_success_criteria'])}", "",
        "## 2. 分层进阶目标", "",
        "| 目标层级 | 具体目标内容 | 相关学生姓名 | 可观察的达成表现 |",
        "|---|---|---|---|",
    ]
    for goal in result["progression_goals"]:
        lines.append(f"| {goal['target_level_name']} | {goal['goal_statement']} | {student_names(goal['target_students'])} | {goal['observable_achievement']} |")
    lines += [
        "", "## 3. 推荐课堂组织与推进", "",
        f"- **主要组织模式：** {path['primary_path_label']}",
        f"- **模式说明：** {path['mode_explanation']}",
        f"- **推荐理由：** {'；'.join(path['recommendation_reasons'])}",
        f"- **课堂推进安排：** {path['teacher_facing_path']}",
    ]
    if path["alternative_path"]:
        alternative = path["alternative_path"]
        lines.append(f"- **备选组织模式：** {alternative['path_label']}；适用条件：{alternative['suitable_when']}；代价：{alternative['tradeoff']}")
    lines += [
        "", "## 4. 课堂活动路径", "",
        "| 阶段 | 活动名称 | 组织形式 | 活动内容简介 | 面向学生与目标 | 建议时长 |",
        "|---|---|---|---|---|---:|",
    ]
    for stage in path["stages"]:
        for unit in stage["activity_units"]:
            lines.append(
                f"| {stage['stage_id']} {stage['stage_name']} | {unit['activity_name']} | "
                f"{unit['organization_name']}（{unit['organization_code']}） | {unit['activity_summary']} | "
                f"{student_names(unit['target_students'])}；{', '.join(unit['target_goal_ids'])} | {stage['duration_minutes']} 分钟 |"
            )
    lines += ["", "同一阶段的并行活动共享阶段时长，不重复相加。", "", "## 5. 请教师确认", ""]
    lines += [f"- {item}" for item in result["teacher_confirmation"]["items_to_confirm"]]
    return "\n".join(lines)


def _serialized_row(teaching_id: str, classroom_id: str) -> dict | None:
    row = fetch_one(
        "SELECT * FROM goal_path_designs WHERE teaching_id = ? AND classroom_id = ?",
        (teaching_id, classroom_id),
    )
    if not row:
        return None
    source = fetch_one(
        "SELECT source_hash, status FROM class_diagnosis_reports WHERE teaching_id = ? AND classroom_id = ?",
        (teaching_id, classroom_id),
    )
    stale = not source or source["status"] != "ready" or source["source_hash"] != row["class_source_hash"]
    analysis = fetch_one(
        "SELECT * FROM goal_path_analysis_drafts WHERE teaching_id = ? AND classroom_id = ?",
        (teaching_id, classroom_id),
    )
    if analysis and not stale:
        context = json.loads(row["teacher_context_json"])
        stale = (
            analysis["class_source_hash"] != row["class_source_hash"] or
            analysis["content_analysis"] != context["teaching_content_and_curriculum_analysis"] or
            analysis["focus_analysis"] != context["teaching_focus_and_difficulty_analysis"]
        )
    return {
        "teaching_id": teaching_id, "classroom_id": classroom_id,
        "skill_key": row["skill_key"], "skill_version": row["skill_version"],
        "provider": row["provider"], "status": "stale" if stale else row["status"],
        "result": json.loads(row["generated_json"]),
        "report_text": row["report_text"],
        "teacher_instructional_context": json.loads(row["teacher_context_json"]),
        "generated_at": row["generated_at"], "confirmed_at": row["confirmed_at"],
        "updated_at": row["updated_at"],
    }


def _analysis_draft(teaching_id: str, classroom_id: str) -> dict | None:
    row = fetch_one(
        "SELECT * FROM goal_path_analysis_drafts WHERE teaching_id = ? AND classroom_id = ?",
        (teaching_id, classroom_id),
    )
    report = fetch_one(
        "SELECT source_hash, status FROM class_diagnosis_reports WHERE teaching_id = ? AND classroom_id = ?",
        (teaching_id, classroom_id),
    )
    if not row or not report or report["status"] != "ready" or row["class_source_hash"] != report["source_hash"]:
        return None
    return {
        "content_analysis": row["content_analysis"], "focus_analysis": row["focus_analysis"],
        "source_note": row["source_note"], "content_confirmed": bool(row["content_confirmed"]),
        "focus_confirmed": bool(row["focus_confirmed"]), "provider": row["provider"],
        "updated_at": row["updated_at"],
    }


@router.get("/precision-teachings/{teaching_id}/classrooms/{classroom_id}/goal-path")
def get_goal_path(teaching_id: str, classroom_id: str) -> dict:
    report = fetch_one(
        "SELECT status FROM class_diagnosis_reports WHERE teaching_id = ? AND classroom_id = ?",
        (teaching_id, classroom_id),
    )
    return {"class_diagnosis_ready": bool(report and report["status"] == "ready"),
            "design": _serialized_row(teaching_id, classroom_id),
            "teacher_analysis": _analysis_draft(teaching_id, classroom_id)}


@router.post("/precision-teachings/{teaching_id}/classrooms/{classroom_id}/goal-path/teacher-analysis/generate")
def generate_teacher_analysis(teaching_id: str, classroom_id: str) -> dict:
    report = _source_report(teaching_id, classroom_id)
    teaching = fetch_one("SELECT * FROM precision_teachings WHERE id = ?", (teaching_id,))
    class_result = json.loads(report["generated_json"])
    _student_levels(class_result)
    summary = class_result["class_summary"]
    payload = {
        "precision_teaching": {
            "subject": teaching["subject"], "grade": teaching["grade"],
            "textbook": teaching["textbook"], "topic": teaching["title"],
            "goal": teaching["goal"], "content": teaching["content"],
        },
        "class_diagnosis": {
            "total_students": summary["total_students"],
            "distribution": [{"level": item["level"], "count": item["count"]} for item in summary["distribution"]],
            "overall_diagnosis": class_result["overall_diagnosis"],
            "teaching_priorities": class_result["teaching_priorities"],
            "intervention_design_basis": class_result["intervention_design_basis"],
        },
    }
    try:
        draft = run_goal_path_teacher_analysis(_replace_names(payload, _aliases(class_result)), settings.ai_provider)
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"教学分析草稿生成失败：{error}") from error
    now = utc_now()
    with database() as connection:
        connection.execute(
            """
            INSERT INTO goal_path_analysis_drafts
            (teaching_id, classroom_id, class_source_hash, content_analysis, focus_analysis,
             source_note, content_confirmed, focus_confirmed, provider, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?)
            ON CONFLICT(teaching_id, classroom_id) DO UPDATE SET
                class_source_hash = excluded.class_source_hash, content_analysis = excluded.content_analysis,
                focus_analysis = excluded.focus_analysis, source_note = excluded.source_note,
                content_confirmed = 0, focus_confirmed = 0, provider = excluded.provider,
                updated_at = excluded.updated_at
            """,
            (teaching_id, classroom_id, report["source_hash"], draft["content_analysis"],
             draft["focus_analysis"], draft["source_note"], settings.ai_provider, now),
        )
    write_audit("generate", "goal_path_teacher_analysis", f"{teaching_id}:{classroom_id}", {})
    return {"teacher_analysis": _analysis_draft(teaching_id, classroom_id)}


@router.put("/precision-teachings/{teaching_id}/classrooms/{classroom_id}/goal-path/teacher-analysis")
def save_teacher_analysis(teaching_id: str, classroom_id: str, request: TeacherAnalysisSaveRequest) -> dict:
    report = _source_report(teaching_id, classroom_id)
    existing = _analysis_draft(teaching_id, classroom_id)
    now = utc_now()
    with database() as connection:
        connection.execute(
            """
            INSERT INTO goal_path_analysis_drafts
            (teaching_id, classroom_id, class_source_hash, content_analysis, focus_analysis,
             source_note, content_confirmed, focus_confirmed, provider, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, 1, ?, ?)
            ON CONFLICT(teaching_id, classroom_id) DO UPDATE SET
                class_source_hash = excluded.class_source_hash,
                content_analysis = excluded.content_analysis,
                focus_analysis = excluded.focus_analysis,
                content_confirmed = 1, focus_confirmed = 1, updated_at = excluded.updated_at
            """,
            (teaching_id, classroom_id, report["source_hash"], request.content_analysis,
             request.focus_analysis, existing["source_note"] if existing else "教师填写",
             existing["provider"] if existing else "manual", now),
        )
    write_audit("save", "goal_path_teacher_analysis", f"{teaching_id}:{classroom_id}", {})
    return {"teacher_analysis": _analysis_draft(teaching_id, classroom_id)}


@router.post("/precision-teachings/{teaching_id}/classrooms/{classroom_id}/goal-path/generate")
def generate_goal_path(teaching_id: str, classroom_id: str, request: GoalPathGenerateRequest) -> dict:
    analysis = _analysis_draft(teaching_id, classroom_id)
    context = request.teacher_instructional_context
    if not analysis or (
        analysis["content_analysis"] != context.teaching_content_and_curriculum_analysis or
        analysis["focus_analysis"] != context.teaching_focus_and_difficulty_analysis
    ):
        raise HTTPException(status_code=409, detail="请先保存当前班级的两项教学分析")
    skill = load_skill("precision_intervention_goal_path_design")
    teacher_context = request.teacher_instructional_context.model_dump()
    try:
        import jsonschema
        payload, class_result, class_report = _build_input(teaching_id, classroom_id, teacher_context)
        schema = json.loads((skill.path.parent / "assets/goal-path-input.schema.json").read_text(encoding="utf-8"))
        jsonschema.validate(payload, schema)
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=422, detail=f"目标与路径输入校验失败：{error}") from error
    input_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    input_hash = hashlib.sha256(input_json.encode("utf-8")).hexdigest()
    job_id = f"job-{uuid4().hex}"
    with database() as connection:
        connection.execute(
            "INSERT INTO ai_jobs (id, skill_key, skill_version, provider, status, input_json, created_at) VALUES (?, ?, ?, ?, 'running', ?, ?)",
            (job_id, skill.key, skill.version, settings.ai_provider, input_json, utc_now()),
        )
    try:
        result, report_text = run_goal_path_design(skill, payload, settings.ai_provider)
        aliases = _aliases(class_result)
        result = _replace_names(result, {alias: real for real, alias in aliases.items()})
        report_text = _replace_names(report_text, {alias: real for real, alias in aliases.items()})
        result["design_status"] = "pending_teacher_confirmation"
        result["teacher_confirmation"]["status"] = "pending_teacher_confirmation"
        _validate_design(result, class_result, teacher_context["planned_duration_minutes"])
        now = utc_now()
        output_json = json.dumps(result, ensure_ascii=False)
        with database() as connection:
            connection.execute(
                "UPDATE ai_jobs SET status = 'completed', output_json = ?, completed_at = ? WHERE id = ?",
                (output_json, now, job_id),
            )
            connection.execute(
                """
                INSERT INTO goal_path_designs
                (teaching_id, classroom_id, skill_key, skill_version, provider, status,
                 class_source_hash, input_hash, teacher_context_json, generated_json, report_text,
                 generated_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'pending_teacher_confirmation', ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(teaching_id, classroom_id) DO UPDATE SET
                    skill_key = excluded.skill_key, skill_version = excluded.skill_version,
                    provider = excluded.provider, status = excluded.status,
                    class_source_hash = excluded.class_source_hash, input_hash = excluded.input_hash,
                    teacher_context_json = excluded.teacher_context_json, generated_json = excluded.generated_json,
                    report_text = excluded.report_text, generated_at = excluded.generated_at,
                    confirmed_at = NULL, updated_at = excluded.updated_at
                """,
                (teaching_id, classroom_id, skill.key, skill.version, settings.ai_provider,
                 class_report["source_hash"], input_hash, json.dumps(teacher_context, ensure_ascii=False),
                 output_json, report_text, now, now),
            )
        write_audit("generate", "goal_path_design", f"{teaching_id}:{classroom_id}", {"job_id": job_id})
        return {"design": _serialized_row(teaching_id, classroom_id)}
    except Exception as error:
        with database() as connection:
            connection.execute(
                "UPDATE ai_jobs SET status = 'failed', error = ?, completed_at = ? WHERE id = ?",
                (str(error), utc_now(), job_id),
            )
        raise HTTPException(status_code=502, detail=f"目标与路径生成失败：{error}") from error


@router.put("/precision-teachings/{teaching_id}/classrooms/{classroom_id}/goal-path")
def save_goal_path(teaching_id: str, classroom_id: str, request: GoalPathSaveRequest) -> dict:
    existing = _serialized_row(teaching_id, classroom_id)
    if not existing or existing["status"] == "stale":
        raise HTTPException(status_code=409, detail="方案不存在或班级诊断已更新，请重新生成")
    class_result = json.loads(_source_report(teaching_id, classroom_id)["generated_json"])
    result = copy.deepcopy(request.result)
    result["design_status"] = "teacher_confirmed" if request.confirmed else "pending_teacher_confirmation"
    result["teacher_confirmation"]["status"] = result["design_status"]
    try:
        _validate_design(result, class_result, existing["teacher_instructional_context"]["planned_duration_minutes"])
    except Exception as error:
        raise HTTPException(status_code=422, detail=f"方案校验失败：{error}") from error
    now = utc_now()
    with database() as connection:
        connection.execute(
            """
            UPDATE goal_path_designs SET status = ?, generated_json = ?, report_text = ?,
                confirmed_at = ?, updated_at = ?
            WHERE teaching_id = ? AND classroom_id = ?
            """,
            (result["design_status"], json.dumps(result, ensure_ascii=False), _markdown(result),
             now if request.confirmed else None, now, teaching_id, classroom_id),
        )
    write_audit("confirm" if request.confirmed else "save", "goal_path_design", f"{teaching_id}:{classroom_id}", {})
    return {"design": _serialized_row(teaching_id, classroom_id)}
