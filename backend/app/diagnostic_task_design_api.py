from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from .config import settings
from .database import database, fetch_one, utc_now, write_audit
from .services.ai import run_diagnostic_task_design
from .services.skills import load_skill


router = APIRouter(prefix="/api/precision-teachings")


def _source(teaching_id: str) -> tuple[dict, str, str]:
    teaching = fetch_one("SELECT * FROM precision_teachings WHERE id = ?", (teaching_id,))
    if not teaching:
        raise HTTPException(status_code=404, detail="精准教学不存在")
    required = {
        "精准教学主题": teaching["title"],
        "精准教学目标": teaching["goal"],
        "精准教学内容": teaching["content"],
    }
    missing = [name for name, value in required.items() if not str(value).strip()]
    if missing:
        raise HTTPException(status_code=422, detail=f"请先补充：{'、'.join(missing)}")
    skill = load_skill("precision_diagnostic_task_design")
    contents = [line.strip() for line in teaching["content"].splitlines() if line.strip()]
    payload = {
        "precision_teaching_context": {
            "subject": teaching["subject"] or "未提供",
            "grade": teaching["grade"] or "未提供",
            "textbook_version_and_chapter": teaching["textbook"] or "未提供",
            "precision_teaching_topic": teaching["title"],
            "precision_teaching_goals": [teaching["goal"]],
            "precision_teaching_content": contents,
        }
    }
    source_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    source_hash = hashlib.sha256(f"{skill.version}\n{source_json}".encode("utf-8")).hexdigest()
    return payload, source_hash, skill.version


@router.get("/{teaching_id}/diagnostic-task-recommendations")
def get_recommendations(teaching_id: str) -> dict:
    _, source_hash, version = _source(teaching_id)
    row = fetch_one("SELECT * FROM diagnostic_task_recommendations WHERE teaching_id = ?", (teaching_id,))
    if not row:
        return {"status": "not_generated", "result": None}
    if row["input_hash"] != source_hash or row["skill_version"] != version:
        return {"status": "stale", "result": None}
    return {"status": "ready", "result": json.loads(row["generated_json"]), "generated_at": row["generated_at"]}


@router.post("/{teaching_id}/diagnostic-task-recommendations/generate")
def generate_recommendations(teaching_id: str) -> dict:
    payload, source_hash, _ = _source(teaching_id)
    skill = load_skill("precision_diagnostic_task_design")
    job_id = f"job-{uuid4().hex}"
    with database() as connection:
        connection.execute(
            "INSERT INTO ai_jobs (id, skill_key, skill_version, provider, status, input_json, created_at) VALUES (?, ?, ?, ?, 'running', ?, ?)",
            (job_id, skill.key, skill.version, settings.ai_provider, json.dumps(payload, ensure_ascii=False), utc_now()),
        )
    try:
        result = run_diagnostic_task_design(skill, payload, settings.ai_provider)
    except Exception as error:
        with database() as connection:
            connection.execute(
                "UPDATE ai_jobs SET status = 'failed', error = ?, completed_at = ? WHERE id = ?",
                (str(error), utc_now(), job_id),
            )
        raise HTTPException(status_code=502, detail=f"诊断任务生成失败：{error}") from error
    now = utc_now()
    result_json = json.dumps(result, ensure_ascii=False)
    with database() as connection:
        connection.execute(
            "UPDATE ai_jobs SET status = 'completed', output_json = ?, completed_at = ? WHERE id = ?",
            (result_json, now, job_id),
        )
        connection.execute(
            """INSERT INTO diagnostic_task_recommendations
               (teaching_id, skill_version, provider, input_hash, generated_json, generated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(teaching_id) DO UPDATE SET
                 skill_version = excluded.skill_version, provider = excluded.provider,
                 input_hash = excluded.input_hash, generated_json = excluded.generated_json,
                 generated_at = excluded.generated_at""",
            (teaching_id, skill.version, settings.ai_provider, source_hash, result_json, now),
        )
    write_audit("generate", "diagnostic_task_recommendations", teaching_id, {"skill_version": skill.version})
    return {"status": "ready", "result": result, "generated_at": now}
