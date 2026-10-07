from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from .config import settings
from .database import database, fetch_all, fetch_one, utc_now, write_audit
from .schemas import TeacherAnalysisStandardUpdate
from .services.ai import run_teacher_custom_analysis
from .services.skills import load_skill


router = APIRouter(prefix="/api")


def standard_for(teaching_id: str) -> dict:
    row = fetch_one("SELECT * FROM teacher_analysis_standards WHERE teaching_id = ?", (teaching_id,))
    return {
        "criteria": row["criteria"] if row else "",
        "individual_enabled": bool(row["individual_enabled"]) if row else False,
        "class_enabled": bool(row["class_enabled"]) if row else False,
        "updated_at": row["updated_at"] if row else None,
    }


@router.put("/precision-teachings/{teaching_id}/custom-analysis/standard")
def save_standard(teaching_id: str, payload: TeacherAnalysisStandardUpdate) -> dict:
    if not fetch_one("SELECT 1 FROM precision_teachings WHERE id = ?", (teaching_id,)):
        raise HTTPException(status_code=404, detail="精准教学不存在")
    criteria = payload.criteria.strip()
    if (payload.individual_enabled or payload.class_enabled) and not criteria:
        raise HTTPException(status_code=422, detail="请选择反馈范围前，先填写教师自定义分析标准")
    now = utc_now()
    with database() as connection:
        connection.execute(
            """INSERT INTO teacher_analysis_standards
               (teaching_id, criteria, individual_enabled, class_enabled, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(teaching_id) DO UPDATE SET
                 criteria = excluded.criteria,
                 individual_enabled = excluded.individual_enabled,
                 class_enabled = excluded.class_enabled,
                 updated_at = excluded.updated_at""",
            (teaching_id, criteria, int(payload.individual_enabled), int(payload.class_enabled), now),
        )
    write_audit("save", "teacher_analysis_standard", teaching_id, {
        "individual_enabled": payload.individual_enabled, "class_enabled": payload.class_enabled,
    })
    return {"standard": standard_for(teaching_id)}


def build_custom_input(teaching_id: str, mode: str, subject_id: str) -> tuple[dict, str]:
    if mode not in {"individual", "class"}:
        raise HTTPException(status_code=404, detail="未知的自定义分析范围")
    standard = standard_for(teaching_id)
    if not standard["criteria"] or not standard[f"{mode}_enabled"]:
        raise HTTPException(status_code=409, detail="请先保存并启用该范围的教师自定义分析标准")
    teaching = fetch_one(
        """SELECT p.subject, p.grade, p.title, p.goal, d.task_text
           FROM precision_teachings p JOIN diagnosis_tasks d ON d.teaching_id = p.id
           WHERE p.id = ? AND d.status = 'published'""",
        (teaching_id,),
    )
    if not teaching:
        raise HTTPException(status_code=409, detail="请先发布诊断任务")
    scope_clause = "AND sts.id = ?" if mode == "individual" else "AND s.classroom_id = ?"
    rows = fetch_all(
        f"""SELECT sts.id AS session_id, sts.submitted_text, s.id AS student_id,
                   s.classroom_id, s.name AS student_name
            FROM student_task_sessions sts
            JOIN students s ON s.id = sts.student_id
            JOIN diagnosis_assignments da ON da.teaching_id = sts.teaching_id
              AND da.classroom_id = s.classroom_id AND da.is_published = 1
            WHERE sts.teaching_id = ? AND sts.status = 'submitted' {scope_clause}
            ORDER BY s.id""",
        (teaching_id, subject_id),
    )
    if not rows:
        raise HTTPException(status_code=409, detail="所选范围暂无已提交的学生记录")
    students = []
    for row in rows:
        messages = fetch_all(
            "SELECT id, role, content FROM student_messages WHERE session_id = ? ORDER BY id",
            (row["session_id"],),
        )
        sources = [
            {"source_id": f"turn-{item['id']}", "role": item["role"], "content": item["content"]}
            for item in messages
        ]
        if row["submitted_text"] and row["submitted_text"].strip():
            sources.append({"source_id": f"submission-{row['session_id']}", "role": "student", "content": row["submitted_text"]})
        attachment_count = fetch_one(
            "SELECT COUNT(*) AS count FROM student_submissions WHERE session_id = ?",
            (row["session_id"],),
        )["count"]
        students.append({
            "student_id": row["student_id"], "evidence_sources": sources,
            "unread_attachment_count": attachment_count,
        })
    payload = {
        "mode": mode,
        "teacher_criteria": standard["criteria"],
        "teaching_context": {key: teaching[key] for key in ("subject", "grade", "title", "goal", "task_text")},
        "students": students,
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if len(serialized) > 350_000:
        raise HTTPException(status_code=413, detail="本次分析的原始文本过长，请缩小班级范围或分批分析")
    return payload, hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def custom_report_for(teaching_id: str, mode: str, subject_id: str) -> dict | None:
    row = fetch_one(
        "SELECT * FROM teacher_analysis_reports WHERE teaching_id = ? AND scope = ? AND subject_id = ?",
        (teaching_id, mode, subject_id),
    )
    if not row:
        return None
    try:
        current_input, current_hash = build_custom_input(teaching_id, mode, subject_id)
        status = "ready" if row["input_hash"] == current_hash else "stale"
        if status == "stale":
            source_job = fetch_one(
                """SELECT input_json FROM ai_jobs
                   WHERE skill_key = ? AND status = 'completed' AND completed_at = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (row["skill_key"], row["generated_at"]),
            )
            if source_job:
                previous_input = json.loads(source_job["input_json"])
                previous_evidence = {key: value for key, value in previous_input.items() if key != "teaching_context"}
                current_evidence = {key: value for key, value in current_input.items() if key != "teaching_context"}
                if previous_evidence == current_evidence:
                    with database() as connection:
                        updated = connection.execute(
                            """UPDATE teacher_analysis_reports SET input_hash = ?
                               WHERE teaching_id = ? AND scope = ? AND subject_id = ?
                                 AND input_hash = ? AND generated_at = ?""",
                            (current_hash, teaching_id, mode, subject_id, row["input_hash"], row["generated_at"]),
                        )
                    if updated.rowcount:
                        status = "ready"
    except HTTPException:
        status = "stale"
    return {
        "scope": mode,
        "subject_id": subject_id,
        "status": status,
        "provider": row["provider"],
        "result": json.loads(row["result_json"]),
        "generated_at": row["generated_at"],
    }


@router.post("/precision-teachings/{teaching_id}/custom-analysis/{mode}/{subject_id}/generate")
def generate_custom_report(teaching_id: str, mode: str, subject_id: str) -> dict:
    payload, input_hash = build_custom_input(teaching_id, mode, subject_id)
    skill = load_skill("teacher_custom_analysis")
    job_id = f"job-{uuid4().hex}"
    with database() as connection:
        connection.execute(
            """INSERT INTO ai_jobs (id, skill_key, skill_version, provider, status, input_json, created_at)
               VALUES (?, ?, ?, ?, 'running', ?, ?)""",
            (job_id, skill.key, skill.version, settings.ai_provider,
             json.dumps(payload, ensure_ascii=False), utc_now()),
        )
    try:
        result = run_teacher_custom_analysis(skill, payload, settings.ai_provider)
        now = utc_now()
        with database() as connection:
            connection.execute(
                "UPDATE ai_jobs SET status = 'completed', output_json = ?, completed_at = ? WHERE id = ?",
                (json.dumps(result, ensure_ascii=False), now, job_id),
            )
            connection.execute(
                """INSERT INTO teacher_analysis_reports
                   (teaching_id, scope, subject_id, skill_key, skill_version, provider,
                    input_hash, result_json, generated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(teaching_id, scope, subject_id) DO UPDATE SET
                     skill_key = excluded.skill_key, skill_version = excluded.skill_version,
                     provider = excluded.provider, input_hash = excluded.input_hash,
                     result_json = excluded.result_json, generated_at = excluded.generated_at""",
                (teaching_id, mode, subject_id, skill.key, skill.version, settings.ai_provider,
                 input_hash, json.dumps(result, ensure_ascii=False), now),
            )
        write_audit("generate", "teacher_custom_analysis", subject_id, {"job_id": job_id, "mode": mode})
        return {"job_id": job_id, "report": custom_report_for(teaching_id, mode, subject_id)}
    except Exception as error:
        with database() as connection:
            connection.execute(
                "UPDATE ai_jobs SET status = 'failed', error = ?, completed_at = ? WHERE id = ?",
                (str(error)[:1000], utc_now(), job_id),
            )
        raise HTTPException(status_code=502, detail=f"教师自定义分析生成失败：{error}") from error
