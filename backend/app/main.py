from __future__ import annotations

import hashlib
import json
import re
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .database import database, fetch_all, fetch_one, initialize_database, utc_now, write_audit
from .database import teacher_identity_key
from .schemas import (
    AIJobCreate,
    AIJobResponse,
    ClassroomCreate,
    ClassroomUpdate,
    CurrentTeachingUpdate,
    DiagnosisRubricUpdate,
    DiagnosisTaskUpdate,
    InterventionPlanUpdate,
    PrecisionTeachingCreate,
    PrecisionTeachingDraft,
    PrecisionTeachingUpdate,
    TeacherProfileUpdate,
)
from .services.ai import run_skill, validate_task_rubric
from .services.skills import list_skills, load_skill, sync_skills
from .student_api import router as student_router
from .goal_path_api import router as goal_path_router
from .activity_formative_api import router as activity_formative_router
from .intervention_integration_api import router as intervention_integration_router
from .diagnostic_task_design_api import router as diagnostic_task_design_router
from .custom_analysis_api import router as custom_analysis_router, standard_for
from .teacher_auth import current_teacher, router as teacher_auth_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    settings.upload_path.mkdir(parents=True, exist_ok=True)
    sync_skills()
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.include_router(student_router)
app.include_router(goal_path_router)
app.include_router(activity_formative_router)
app.include_router(intervention_integration_router)
app.include_router(diagnostic_task_design_router)
app.include_router(custom_analysis_router)
app.include_router(teacher_auth_router)


@app.middleware("http")
async def protect_teacher_api(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/"):
        return await call_next(request)
    public = path in {
        "/api/health", "/api/teacher-auth/status", "/api/teacher-auth/login", "/api/teacher-auth/login-options",
    } or path.startswith("/api/student/")
    if not public and current_teacher(request) is None:
        return JSONResponse({"detail": "教师登录状态已失效，请重新登录"}, status_code=401, headers={"Cache-Control": "no-store"})
    teacher = current_teacher(request) if not public else None
    if teacher:
        request.state.teacher_workspace_id = teacher["workspace_id"]
        teaching_match = re.match(r"^/api/precision-teachings/([^/]+)", path)
        if teaching_match:
            teaching = fetch_one("SELECT workspace_id FROM precision_teachings WHERE id = ?", (teaching_match.group(1),))
            if not teaching or teaching["workspace_id"] != teacher["workspace_id"]:
                return JSONResponse({"detail": "精准教学不存在"}, status_code=404)
            classroom_match = re.match(r"^/api/precision-teachings/[^/]+/classrooms/([^/]+)", path)
            if classroom_match:
                assigned = fetch_one(
                    "SELECT 1 FROM diagnosis_assignments WHERE teaching_id = ? AND classroom_id = ? AND is_published = 1",
                    (teaching_match.group(1), classroom_match.group(1)),
                )
                if not assigned:
                    return JSONResponse({"detail": "班级不属于当前精准教学"}, status_code=404)
        session_match = re.match(r"^/api/student-results/([^/]+)", path)
        if session_match:
            owner = fetch_one(
                """SELECT p.workspace_id FROM student_task_sessions s
                   JOIN precision_teachings p ON p.id = s.teaching_id WHERE s.id = ?""",
                (session_match.group(1),),
            )
            if not owner or owner["workspace_id"] != teacher["workspace_id"]:
                return JSONResponse({"detail": "学生结果不存在"}, status_code=404)
        submission_match = re.match(r"^/api/teacher/submissions/([^/]+)", path)
        if submission_match:
            owner = fetch_one(
                """SELECT p.workspace_id FROM student_submissions sub
                   JOIN student_task_sessions s ON s.id = sub.session_id
                   JOIN precision_teachings p ON p.id = s.teaching_id WHERE sub.id = ?""",
                (submission_match.group(1),),
            )
            if not owner or owner["workspace_id"] != teacher["workspace_id"]:
                return JSONResponse({"detail": "文件不存在"}, status_code=404)
        job_match = re.match(r"^/api/ai/jobs/([^/]+)", path)
        if job_match:
            job = fetch_one("SELECT workspace_id FROM ai_jobs WHERE id = ?", (job_match.group(1),))
            if not job or job["workspace_id"] != teacher["workspace_id"]:
                return JSONResponse({"detail": "AI 任务不存在"}, status_code=404)
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not path.startswith("/api/student/"):
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if (origin and origin != expected) or request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "跨站请求已拒绝"}, status_code=403, headers={"Cache-Control": "no-store"})
    response = await call_next(request)
    if not path.startswith("/api/student/"):
        response.headers["Cache-Control"] = "no-store"
    return response


TEACHING_ORDER = "CASE p.status WHEN 'active' THEN 0 WHEN 'completed' THEN 1 ELSE 2 END, p.updated_at DESC"


def ordered_teachings(workspace_id: str | None = None) -> list[dict]:
    return fetch_all(
        f"""
        SELECT p.*,
               COALESCE(d.status, 'draft') AS diagnosis_status,
               CASE WHEN d.status = 'published' THEN COALESCE(f.status, 'pending') ELSE 'locked' END AS feedback_status,
               CASE WHEN f.status = 'confirmed' THEN COALESCE(i.status, 'draft') ELSE 'locked' END AS intervention_status
        FROM precision_teachings p
        LEFT JOIN diagnosis_tasks d ON d.teaching_id = p.id
        LEFT JOIN feedback_summaries f ON f.teaching_id = p.id
        LEFT JOIN intervention_plans i ON i.teaching_id = p.id
        {"WHERE p.workspace_id = ?" if workspace_id else ""}
        ORDER BY {TEACHING_ORDER}
        """,
        (workspace_id,) if workspace_id else (),
    )


def current_teaching_id(teachings: list[dict] | None = None, workspace_id: str | None = None) -> str | None:
    available = teachings if teachings is not None else ordered_teachings(workspace_id)
    if not available:
        return None
    state = fetch_one("SELECT value FROM app_state WHERE key = ?", (f"current_teaching_id:{workspace_id}" if workspace_id else "current_teaching_id",))
    candidate = state["value"] if state else None
    valid_ids = {teaching["id"] for teaching in available}
    return candidate if candidate in valid_ids else available[0]["id"]


def save_current_teaching_id(teaching_id: str, workspace_id: str | None = None) -> None:
    with database() as connection:
        connection.execute(
            """
            INSERT INTO app_state (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (f"current_teaching_id:{workspace_id}" if workspace_id else "current_teaching_id", teaching_id, utc_now()),
        )


def ensure_workspace(teaching_id: str) -> None:
    teaching = fetch_one("SELECT * FROM precision_teachings WHERE id = ?", (teaching_id,))
    if not teaching:
        raise HTTPException(status_code=404, detail="精准教学不存在")
    with database() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO diagnosis_tasks
            (teaching_id, diagnosis_type, goal, task_text, ai_role, duration_minutes, status, updated_at)
            VALUES (?, 'pre', ?, '', ?, 15, 'draft', ?)
            """,
            (
                teaching_id,
                teaching["goal"],
                "中性引导者：只围绕学生已有表达追问理由和关系，不提供公式、步骤、正误方向或答案。",
                utc_now(),
            ),
        )


def serialized_rubric(teaching_id: str) -> dict | None:
    row = fetch_one("SELECT * FROM diagnosis_rubrics WHERE teaching_id = ?", (teaching_id,))
    if not row:
        return None
    return {
        "teaching_id": row["teaching_id"],
        "skill_key": row["skill_key"],
        "skill_version": row["skill_version"],
        "provider": row["provider"],
        "status": row["status"],
        "input_hash": row["input_hash"],
        "rubric": json.loads(row["current_json"]),
        "generated_at": row["generated_at"],
        "confirmed_at": row["confirmed_at"],
        "updated_at": row["updated_at"],
    }


def refresh_feedback_counts(teaching_id: str) -> None:
    result = fetch_one(
        """
        SELECT COUNT(*) AS submitted_count
        FROM student_task_sessions sts
        JOIN students s ON s.id = sts.student_id
        JOIN diagnosis_assignments da ON da.teaching_id = sts.teaching_id
          AND da.classroom_id = s.classroom_id AND da.is_published = 1
        WHERE sts.teaching_id = ? AND sts.status = 'submitted'
        """,
        (teaching_id,),
    )
    count = int(result["submitted_count"] if result else 0)
    with database() as connection:
        connection.execute(
            """
            UPDATE feedback_summaries
            SET has_data = ?, total_students = ?,
                confirmed_count = MIN(confirmed_count, ?), updated_at = ?
            WHERE teaching_id = ? AND status != 'confirmed'
            """,
            (1 if count else 0, count, count, utc_now(), teaching_id),
        )


def workspace_for(teaching_id: str) -> dict:
    ensure_workspace(teaching_id)
    refresh_feedback_counts(teaching_id)
    teaching = next((item for item in ordered_teachings() if item["id"] == teaching_id), None)
    if not teaching:
        raise HTTPException(status_code=404, detail="精准教学不存在")
    diagnosis = fetch_one("SELECT * FROM diagnosis_tasks WHERE teaching_id = ?", (teaching_id,))
    if diagnosis:
        diagnosis["classroom_ids"] = [row["classroom_id"] for row in fetch_all(
            "SELECT classroom_id FROM diagnosis_assignments WHERE teaching_id = ? AND is_published = 1 ORDER BY assigned_at",
            (teaching_id,),
        )]
    feedback = fetch_one("SELECT * FROM feedback_summaries WHERE teaching_id = ?", (teaching_id,))
    intervention = fetch_one("SELECT * FROM intervention_plans WHERE teaching_id = ?", (teaching_id,))
    feedback_accessible = bool(diagnosis and diagnosis["status"] == "published")
    intervention_accessible = bool(feedback and feedback["status"] == "confirmed")
    return {
        "teaching": teaching,
        "diagnosis": diagnosis,
        "rubric": serialized_rubric(teaching_id),
        "custom_analysis_standard": standard_for(teaching_id),
        "feedback": feedback,
        "intervention": intervention,
        "stages": {
            "diagnosis": {"status": diagnosis["status"] if diagnosis else "draft", "accessible": True},
            "feedback": {
                "status": feedback["status"] if feedback_accessible and feedback else "locked",
                "accessible": feedback_accessible,
            },
            "intervention": {
                "status": intervention["status"] if intervention_accessible and intervention else "locked",
                "accessible": intervention_accessible,
            },
        },
    }


@app.get("/api/health")
def health() -> dict[str, str]:
    fetch_one("SELECT 1 AS ok")
    return {
        "status": "ok",
        "database": "sqlite",
        "ai_provider": settings.ai_provider,
        "student_ai_provider": settings.student_ai_provider,
    }


@app.get("/api/bootstrap")
def bootstrap(request: Request) -> dict:
    workspace_id = request.state.teacher_workspace_id
    teacher = fetch_one("SELECT * FROM teacher_workspaces WHERE id = ?", (workspace_id,))
    teachings = ordered_teachings(workspace_id)
    return {
        "teacher": {"id": teacher["id"], "school_name": teacher["school_name"], "display_name": teacher["teacher_name"], "subject": teacher["subject"],
                    "years_experience": teacher["years_experience"], "teaching_style": teacher["teaching_style"],
                    "profile_completed": bool(teacher["profile_completed"])},
        "classrooms": fetch_all(
            """SELECT c.* FROM classrooms c
               JOIN classroom_workspaces cw ON cw.classroom_id = c.id
               WHERE cw.workspace_id = ? ORDER BY c.name""", (workspace_id,)),
        "precision_teachings": teachings,
        "current_teaching_id": current_teaching_id(teachings, workspace_id),
        "skills": list_skills(),
        "runtime": {
            "database": "sqlite",
            "llm_request_debug": settings.app_env == "development",
            "ai_provider": settings.ai_provider,
            "student_ai_provider": settings.student_ai_provider,
            "ai_model": settings.ai_model,
            "student_ai_model": settings.student_ai_model,
        },
    }


@app.get("/api/dev/llm-requests")
def get_development_llm_requests(request: Request, after_id: int = 0) -> JSONResponse:
    if settings.app_env != "development" or request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=404, detail="仅本机开发环境可查看 LLM 请求")
    if fetch_one("SELECT COUNT(*) AS count FROM teacher_workspaces")["count"] > 1:
        return JSONResponse({"requests": []}, headers={"Cache-Control": "no-store"})
    rows = fetch_all(
        "SELECT * FROM llm_request_logs WHERE id > ? ORDER BY id DESC LIMIT 50",
        (max(0, after_id),),
    )
    rows.reverse()
    records = []
    for row in rows:
        row["request"] = json.loads(row.pop("request_json"))
        records.append(row)
    return JSONResponse(
        {"requests": records},
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/teacher-profile")
def get_teacher_profile(request: Request) -> dict:
    profile = fetch_one("SELECT * FROM teacher_workspaces WHERE id = ?", (request.state.teacher_workspace_id,))
    if not profile:
        raise HTTPException(status_code=404, detail="教师信息不存在")
    return {"id": profile["id"], "school_name": profile["school_name"], "display_name": profile["teacher_name"], "subject": profile["subject"],
            "years_experience": profile["years_experience"], "teaching_style": profile["teaching_style"],
            "profile_completed": bool(profile["profile_completed"])}


@app.put("/api/teacher-profile")
def update_teacher_profile(payload: TeacherProfileUpdate, request: Request) -> dict:
    now = utc_now()
    workspace_id = request.state.teacher_workspace_id
    workspace = fetch_one("SELECT school_name FROM teacher_workspaces WHERE id = ?", (workspace_id,))
    key = teacher_identity_key(workspace["school_name"], payload.subject, payload.display_name)
    duplicate = fetch_one("SELECT id FROM teacher_workspaces WHERE identity_key = ?", (key,))
    if duplicate and duplicate["id"] != workspace_id:
        raise HTTPException(status_code=409, detail="该学校、学科、教师姓名对应的空间已存在")
    with database() as connection:
        connection.execute(
            """
            UPDATE teacher_workspaces
            SET teacher_name = ?, subject = ?, identity_key = ?, years_experience = ?, teaching_style = ?, profile_completed = 1, updated_at = ?
            WHERE id = ?
            """,
            (
                payload.display_name,
                payload.subject,
                key,
                payload.years_experience,
                payload.teaching_style,
                now,
                workspace_id,
            ),
        )
    write_audit("update", "teacher_profile", workspace_id, payload.model_dump())
    return get_teacher_profile(request)


@app.get("/api/precision-teachings")
def list_precision_teachings(request: Request) -> list[dict]:
    return ordered_teachings(request.state.teacher_workspace_id)


@app.post("/api/classrooms", status_code=201)
def create_classroom(payload: ClassroomCreate, request: Request) -> dict:
    teacher = fetch_one("SELECT school_name FROM teacher_workspaces WHERE id = ?", (request.state.teacher_workspace_id,))
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="请填写班级名称")
    existing = fetch_one(
        """SELECT c.id FROM classrooms c JOIN classroom_schools cs ON cs.classroom_id = c.id
           WHERE cs.school_name = ? AND c.name = ?""", (teacher["school_name"], name),
    )
    if existing:
        raise HTTPException(status_code=409, detail="该学校已有同名班级")
    classroom_id = f"class-{uuid4().hex[:12]}"
    now = utc_now()
    with database() as connection:
        connection.execute(
            """INSERT INTO classrooms (id, name, student_count, background, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (classroom_id, name, payload.student_count, payload.background.strip(), now, now),
        )
        connection.execute(
            "INSERT INTO classroom_schools (classroom_id, school_name, updated_at) VALUES (?, ?, ?)",
            (classroom_id, teacher["school_name"], now),
        )
        connection.execute(
            "INSERT INTO classroom_workspaces (classroom_id, workspace_id, linked_at) VALUES (?, ?, ?)",
            (classroom_id, request.state.teacher_workspace_id, now),
        )
    write_audit("create", "classroom", classroom_id, {"school": teacher["school_name"], "name": name})
    return fetch_one("SELECT * FROM classrooms WHERE id = ?", (classroom_id,))


@app.put("/api/classrooms/{classroom_id}")
def update_classroom(classroom_id: str, payload: ClassroomUpdate, request: Request) -> dict:
    owned = fetch_one(
        "SELECT 1 FROM classroom_workspaces WHERE classroom_id = ? AND workspace_id = ?",
        (classroom_id, request.state.teacher_workspace_id),
    )
    if not owned:
        raise HTTPException(status_code=404, detail="班级不存在")
    with database() as connection:
        connection.execute(
            "UPDATE classrooms SET student_count = ?, background = ?, updated_at = ? WHERE id = ?",
            (payload.student_count, payload.background.strip(), utc_now(), classroom_id),
        )
    write_audit("update", "classroom", classroom_id, payload.model_dump())
    return fetch_one("SELECT * FROM classrooms WHERE id = ?", (classroom_id,))


@app.get("/api/precision-teachings/{teaching_id}/workspace")
def get_teaching_workspace(teaching_id: str) -> dict:
    return workspace_for(teaching_id)


@app.put("/api/current-teaching")
def update_current_teaching(payload: CurrentTeachingUpdate, request: Request) -> dict:
    teaching = fetch_one("SELECT * FROM precision_teachings WHERE id = ?", (payload.teaching_id,))
    if not teaching or teaching["workspace_id"] != request.state.teacher_workspace_id:
        raise HTTPException(status_code=404, detail="精准教学不存在")
    save_current_teaching_id(payload.teaching_id, request.state.teacher_workspace_id)
    write_audit("select", "precision_teaching", payload.teaching_id, {})
    return teaching


@app.post("/api/precision-teachings", status_code=201)
def create_precision_teaching(payload: PrecisionTeachingCreate, request: Request) -> dict:
    workspace_id = request.state.teacher_workspace_id
    teacher = fetch_one("SELECT profile_completed FROM teacher_workspaces WHERE id = ?", (workspace_id,))
    if not teacher or not teacher["profile_completed"]:
        raise HTTPException(status_code=422, detail="请先保存教师教学信息")
    classroom = fetch_one("SELECT 1 FROM classroom_workspaces WHERE workspace_id = ? LIMIT 1", (workspace_id,))
    if not classroom:
        raise HTTPException(status_code=422, detail="请先添加至少一个班级")
    teaching_id = f"pt-{uuid4().hex[:12]}"
    now = utc_now()
    with database() as connection:
        connection.execute(
            """
            INSERT INTO precision_teachings
            (id, workspace_id, title, goal, content, rationale, subject, grade, textbook,
             estimated_periods, status, current_stage, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', 'diagnosis', ?, ?)
            """,
            (
                teaching_id,
                workspace_id,
                payload.title,
                payload.goal,
                payload.content,
                payload.rationale,
                payload.subject,
                payload.grade,
                payload.textbook,
                payload.estimated_periods,
                now,
                now,
            ),
        )
    write_audit("create", "precision_teaching", teaching_id, payload.model_dump())
    save_current_teaching_id(teaching_id, workspace_id)
    ensure_workspace(teaching_id)
    return next((item for item in ordered_teachings(workspace_id) if item["id"] == teaching_id), {})


@app.post("/api/precision-teaching-drafts", status_code=201)
def create_precision_teaching_draft(payload: PrecisionTeachingDraft, request: Request) -> dict:
    return create_precision_teaching(payload, request)


@app.put("/api/precision-teachings/{teaching_id}")
def update_precision_teaching(teaching_id: str, payload: PrecisionTeachingUpdate, request: Request) -> dict:
    workspace_id = request.state.teacher_workspace_id
    existing = fetch_one("SELECT * FROM precision_teachings WHERE id = ? AND workspace_id = ?", (teaching_id, workspace_id))
    if not existing:
        raise HTTPException(status_code=404, detail="精准教学不存在")
    data = payload.model_dump(exclude={"edit_intent"})
    changed = any(existing[field] != value for field, value in data.items())
    if not changed:
        return next(item for item in ordered_teachings(workspace_id) if item["id"] == teaching_id)
    if existing["status"] != "draft":
        if not data["title"].strip() or not data["goal"].strip() or not data["content"].strip():
            raise HTTPException(status_code=422, detail="已发布的精准教学须保留主题、目标和教学内容")
        if payload.edit_intent != "wording":
            raise HTTPException(status_code=409, detail="请先确认本次仅优化表述；若教学范围或诊断依据发生变化，请新建精准教学")
    now = utc_now()
    with database() as connection:
        connection.execute(
            """UPDATE precision_teachings
               SET title = ?, goal = ?, content = ?, rationale = ?, subject = ?, grade = ?,
                   textbook = ?, estimated_periods = ?, updated_at = ?
               WHERE id = ? AND workspace_id = ?""",
            (data["title"], data["goal"], data["content"], data["rationale"], data["subject"],
             data["grade"], data["textbook"], data["estimated_periods"], now, teaching_id, workspace_id),
        )
        if existing["status"] == "draft" and data["goal"] != existing["goal"]:
            connection.execute(
                "UPDATE diagnosis_tasks SET goal = ?, updated_at = ? WHERE teaching_id = ? AND status = 'draft' AND goal = ?",
                (data["goal"], now, teaching_id, existing["goal"]),
            )
        if existing["status"] == "draft":
            connection.execute(
                "UPDATE diagnosis_rubrics SET status = 'stale', confirmed_at = NULL, updated_at = ? WHERE teaching_id = ?",
                (now, teaching_id),
            )
    write_audit("update", "precision_teaching", teaching_id, {"before": {field: existing[field] for field in data}, "after": data, "edit_intent": payload.edit_intent})
    return next(item for item in ordered_teachings(workspace_id) if item["id"] == teaching_id)


@app.put("/api/precision-teachings/{teaching_id}/diagnosis")
def update_diagnosis(teaching_id: str, payload: DiagnosisTaskUpdate) -> dict:
    ensure_workspace(teaching_id)
    selected_classrooms = list(dict.fromkeys(payload.classroom_ids or []))
    if payload.status == "published":
        if not selected_classrooms:
            raise HTTPException(status_code=422, detail="请至少选择一个发布班级")
        owner = fetch_one("SELECT workspace_id FROM precision_teachings WHERE id = ?", (teaching_id,))
        owned = fetch_all(
            "SELECT classroom_id FROM classroom_workspaces WHERE workspace_id = ?",
            (owner["workspace_id"],),
        )
        if not set(selected_classrooms).issubset({row["classroom_id"] for row in owned}):
            raise HTTPException(status_code=422, detail="发布班级必须来自当前教师的班级列表")
    if payload.status == "published" and (not payload.goal.strip() or not payload.task_text.strip()):
        raise HTTPException(status_code=422, detail="发布前请填写诊断目标和诊断任务")
    previous = fetch_one("SELECT goal, task_text FROM diagnosis_tasks WHERE teaching_id = ?", (teaching_id,))
    rubric_inputs_changed = bool(
        previous and (previous["goal"] != payload.goal or previous["task_text"] != payload.task_text)
    )
    if payload.status == "published" and rubric_inputs_changed:
        raise HTTPException(status_code=409, detail="诊断目标或任务已变化，请返回分析标准步骤重新生成并确认量规")
    if payload.status == "published":
        rubric = fetch_one("SELECT status FROM diagnosis_rubrics WHERE teaching_id = ?", (teaching_id,))
        if not rubric or rubric["status"] != "confirmed":
            raise HTTPException(status_code=409, detail="请先生成并确认本任务的 SOLO 分析标准")
    now = utc_now()
    with database() as connection:
        connection.execute(
            """
            UPDATE diagnosis_tasks
            SET diagnosis_type = ?, goal = ?, task_text = ?, ai_role = ?,
                duration_minutes = ?, status = ?, updated_at = ?
            WHERE teaching_id = ?
            """,
            (
                payload.diagnosis_type,
                payload.goal,
                payload.task_text,
                payload.ai_role,
                payload.duration_minutes,
                payload.status,
                now,
                teaching_id,
            ),
        )
        if rubric_inputs_changed:
            connection.execute(
                "UPDATE diagnosis_rubrics SET status = 'stale', confirmed_at = NULL, updated_at = ? WHERE teaching_id = ?",
                (now, teaching_id),
            )
            connection.execute(
                "UPDATE student_diagnosis_reports SET status = 'stale', reviewed_at = NULL, pushed_at = NULL, updated_at = ? WHERE teaching_id = ?",
                (now, teaching_id),
            )
        if payload.status == "published":
            connection.execute(
                "UPDATE diagnosis_assignments SET is_published = 0 WHERE teaching_id = ?",
                (teaching_id,),
            )
            connection.executemany(
                """INSERT INTO diagnosis_assignments (teaching_id, classroom_id, assigned_at, is_published)
                   VALUES (?, ?, ?, 1)
                   ON CONFLICT(teaching_id, classroom_id) DO UPDATE SET is_published = 1""",
                [(teaching_id, classroom_id, now) for classroom_id in selected_classrooms],
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO feedback_summaries
                (teaching_id, status, has_data, total_students, confirmed_count,
                 evidence_insufficient_count, summary, teacher_judgment, updated_at)
                VALUES (?, 'pending', 0, 0, 0, 0, '', '', ?)
                """,
                (teaching_id, now),
            )
            connection.execute(
                "UPDATE precision_teachings SET status = 'active', current_stage = 'feedback', updated_at = ? WHERE id = ?",
                (now, teaching_id),
            )
    write_audit(payload.status, "diagnosis_task", teaching_id, payload.model_dump())
    return workspace_for(teaching_id)


def build_rubric_input(teaching_id: str) -> dict:
    teaching = fetch_one("SELECT * FROM precision_teachings WHERE id = ?", (teaching_id,))
    diagnosis = fetch_one("SELECT * FROM diagnosis_tasks WHERE teaching_id = ?", (teaching_id,))
    if not teaching or not diagnosis:
        raise HTTPException(status_code=404, detail="精准教学或诊断任务不存在")
    missing = []
    for label, value in (
        ("教材版本与章节", teaching["textbook"]),
        ("精准教学主题", teaching["title"]),
        ("精准教学目标", teaching["goal"]),
        ("精准教学内容", teaching["content"]),
        ("诊断任务", diagnosis["task_text"]),
    ):
        if not str(value).strip():
            missing.append(label)
    if missing:
        raise HTTPException(status_code=422, detail=f"生成量规前请补充：{'、'.join(missing)}")
    contents = [item.strip() for item in str(teaching["content"]).splitlines() if item.strip()]
    return {
        "precision_teaching_context": {
            "subject": teaching["subject"],
            "grade": teaching["grade"],
            "textbook_version_and_chapter": teaching["textbook"],
            "precision_teaching_topic": teaching["title"],
            "precision_teaching_goals": [teaching["goal"]],
            "precision_teaching_content": contents or [teaching["content"]],
        },
        "diagnostic_task": {
            "task_text": diagnosis["task_text"],
            "requirements": ["与 AI 对话，完整说明自己的想法和理由", "提交完整的文字成果"],
        },
    }


@app.post("/api/precision-teachings/{teaching_id}/rubric/generate")
def generate_diagnosis_rubric(teaching_id: str) -> dict:
    ensure_workspace(teaching_id)
    skill = load_skill("solo_task_rubric_builder")
    skill_input = build_rubric_input(teaching_id)
    input_json = json.dumps(skill_input, ensure_ascii=False, sort_keys=True)
    input_hash = hashlib.sha256(input_json.encode("utf-8")).hexdigest()
    job_id = f"job-{uuid4().hex}"
    now = utc_now()
    with database() as connection:
        connection.execute(
            """
            INSERT INTO ai_jobs
            (id, skill_key, skill_version, provider, status, input_json, created_at)
            VALUES (?, ?, ?, ?, 'running', ?, ?)
            """,
            (job_id, skill.key, skill.version, settings.ai_provider, input_json, now),
        )
    try:
        output = run_skill(skill, skill_input, settings.ai_provider)
        completed_at = utc_now()
        output_json = json.dumps(output, ensure_ascii=False)
        with database() as connection:
            connection.execute(
                "UPDATE ai_jobs SET status = 'completed', output_json = ?, completed_at = ? WHERE id = ?",
                (output_json, completed_at, job_id),
            )
            connection.execute(
                """
                INSERT INTO diagnosis_rubrics
                (teaching_id, skill_key, skill_version, provider, status, input_hash,
                 generated_json, current_json, generated_at, confirmed_at, updated_at)
                VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?, NULL, ?)
                ON CONFLICT(teaching_id) DO UPDATE SET
                    skill_key = excluded.skill_key,
                    skill_version = excluded.skill_version,
                    provider = excluded.provider,
                    status = 'draft',
                    input_hash = excluded.input_hash,
                    generated_json = excluded.generated_json,
                    current_json = excluded.current_json,
                    generated_at = excluded.generated_at,
                    confirmed_at = NULL,
                    updated_at = excluded.updated_at
                """,
                (teaching_id, skill.key, skill.version, settings.ai_provider, input_hash, output_json, output_json, completed_at, completed_at),
            )
            connection.execute(
                "UPDATE student_diagnosis_reports SET status = 'stale', reviewed_at = NULL, pushed_at = NULL, updated_at = ? WHERE teaching_id = ?",
                (completed_at, teaching_id),
            )
        write_audit("generate", "diagnosis_rubric", teaching_id, {"job_id": job_id, "skill": skill.key, "version": skill.version})
        return {"job_id": job_id, "rubric": serialized_rubric(teaching_id)}
    except Exception as error:
        with database() as connection:
            connection.execute(
                "UPDATE ai_jobs SET status = 'failed', error = ?, completed_at = ? WHERE id = ?",
                (str(error), utc_now(), job_id),
            )
        raise HTTPException(status_code=502, detail=f"量规生成失败：{error}") from error


@app.put("/api/precision-teachings/{teaching_id}/rubric")
def update_diagnosis_rubric(teaching_id: str, payload: DiagnosisRubricUpdate) -> dict:
    current = fetch_one("SELECT * FROM diagnosis_rubrics WHERE teaching_id = ?", (teaching_id,))
    if not current:
        raise HTTPException(status_code=404, detail="请先生成任务专用量规")
    skill = load_skill(current["skill_key"])
    try:
        validate_task_rubric(skill, payload.rubric)
    except Exception as error:
        raise HTTPException(status_code=422, detail=f"量规格式校验失败：{error}") from error
    now = utc_now()
    status = "confirmed" if payload.confirmed else "draft"
    serialized = json.dumps(payload.rubric, ensure_ascii=False)
    rubric_changed = serialized != current["current_json"]
    with database() as connection:
        connection.execute(
            """
            UPDATE diagnosis_rubrics
            SET current_json = ?, status = ?, confirmed_at = ?, updated_at = ?
            WHERE teaching_id = ?
            """,
            (serialized, status, now if payload.confirmed else None, now, teaching_id),
        )
        if rubric_changed:
            connection.execute(
                "UPDATE student_diagnosis_reports SET status = 'stale', reviewed_at = NULL, pushed_at = NULL, updated_at = ? WHERE teaching_id = ?",
                (now, teaching_id),
            )
    write_audit(status, "diagnosis_rubric", teaching_id, {"skill": skill.key, "version": skill.version})
    return {"rubric": serialized_rubric(teaching_id)}


@app.post("/api/precision-teachings/{teaching_id}/feedback/confirm")
def confirm_feedback(teaching_id: str) -> dict:
    current = workspace_for(teaching_id)
    feedback = current["feedback"]
    if not current["stages"]["feedback"]["accessible"]:
        raise HTTPException(status_code=409, detail="请先发布诊断任务")
    if not feedback or not feedback["has_data"]:
        raise HTTPException(status_code=409, detail="尚无学生提交数据，不能完成反馈阶段")
    now = utc_now()
    with database() as connection:
        connection.execute(
            "UPDATE feedback_summaries SET status = 'confirmed', confirmed_count = total_students, updated_at = ? WHERE teaching_id = ?",
            (now, teaching_id),
        )
        connection.execute(
            """
            INSERT INTO intervention_plans
            (teaching_id, status, duration_minutes, evidence_summary, teacher_judgment, updated_at)
            VALUES (?, 'draft', 45, ?, ?, ?)
            ON CONFLICT(teaching_id) DO UPDATE SET
                evidence_summary = excluded.evidence_summary,
                teacher_judgment = excluded.teacher_judgment,
                updated_at = excluded.updated_at
            """,
            (teaching_id, feedback["summary"], feedback["teacher_judgment"], now),
        )
        connection.execute(
            "UPDATE precision_teachings SET current_stage = 'intervention', updated_at = ? WHERE id = ?",
            (now, teaching_id),
        )
    write_audit("confirm", "feedback_summary", teaching_id, {})
    return workspace_for(teaching_id)


@app.put("/api/precision-teachings/{teaching_id}/intervention")
def update_intervention(teaching_id: str, payload: InterventionPlanUpdate) -> dict:
    current = workspace_for(teaching_id)
    if not current["stages"]["intervention"]["accessible"]:
        raise HTTPException(status_code=409, detail="请先完成诊断结果反馈")
    now = utc_now()
    with database() as connection:
        connection.execute(
            """
            INSERT INTO intervention_plans
            (teaching_id, status, duration_minutes, evidence_summary, teacher_judgment, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(teaching_id) DO UPDATE SET
                status = excluded.status,
                duration_minutes = excluded.duration_minutes,
                evidence_summary = excluded.evidence_summary,
                teacher_judgment = excluded.teacher_judgment,
                updated_at = excluded.updated_at
            """,
            (
                teaching_id,
                payload.status,
                payload.duration_minutes,
                payload.evidence_summary,
                payload.teacher_judgment,
                now,
            ),
        )
        if payload.status == "completed":
            connection.execute(
                "UPDATE precision_teachings SET status = 'completed', updated_at = ? WHERE id = ?",
                (now, teaching_id),
            )
    write_audit(payload.status, "intervention_plan", teaching_id, payload.model_dump())
    return workspace_for(teaching_id)


@app.delete("/api/precision-teachings/{teaching_id}")
def delete_precision_teaching(teaching_id: str, request: Request) -> dict:
    teaching = fetch_one("SELECT * FROM precision_teachings WHERE id = ?", (teaching_id,))
    if not teaching:
        raise HTTPException(status_code=404, detail="精准教学不存在")
    with database() as connection:
        connection.execute("DELETE FROM precision_teachings WHERE id = ?", (teaching_id,))
    workspace_id = request.state.teacher_workspace_id
    state_key = f"current_teaching_id:{workspace_id}"
    remaining = ordered_teachings(workspace_id)
    next_id = current_teaching_id(remaining, workspace_id)
    state = fetch_one("SELECT value FROM app_state WHERE key = ?", (state_key,))
    if state and state["value"] == teaching_id:
        next_id = remaining[0]["id"] if remaining else None
    if next_id:
        save_current_teaching_id(next_id, workspace_id)
    else:
        with database() as connection:
            connection.execute("DELETE FROM app_state WHERE key = ?", (state_key,))
    write_audit("delete", "precision_teaching", teaching_id, {"title": teaching["title"]})
    return {"deleted_id": teaching_id, "current_teaching_id": next_id}


@app.get("/api/skills")
def get_skills() -> list[dict[str, str]]:
    return list_skills()


@app.post("/api/ai/jobs", response_model=AIJobResponse, status_code=201)
def create_ai_job(payload: AIJobCreate, request: Request) -> AIJobResponse:
    job_id = f"job-{uuid4().hex}"
    created_at = utc_now()
    try:
        skill = load_skill(payload.skill_key)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    with database() as connection:
        connection.execute(
            """
            INSERT INTO ai_jobs
            (id, workspace_id, skill_key, skill_version, provider, status, input_json, created_at)
            VALUES (?, ?, ?, ?, ?, 'running', ?, ?)
            """,
            (
                job_id,
                request.state.teacher_workspace_id,
                skill.key,
                skill.version,
                settings.ai_provider,
                json.dumps(payload.input, ensure_ascii=False),
                created_at,
            ),
        )

    try:
        output = run_skill(skill, payload.input, settings.ai_provider)
        completed_at = utc_now()
        with database() as connection:
            connection.execute(
                """
                UPDATE ai_jobs
                SET status = 'completed', output_json = ?, completed_at = ?
                WHERE id = ?
                """,
                (json.dumps(output, ensure_ascii=False), completed_at, job_id),
            )
        write_audit("run", "ai_job", job_id, {"skill": skill.key, "version": skill.version})
        return AIJobResponse(
            id=job_id,
            skill_key=skill.key,
            skill_version=skill.version,
            provider=settings.ai_provider,
            status="completed",
            output=output,
        )
    except Exception as error:
        with database() as connection:
            connection.execute(
                "UPDATE ai_jobs SET status = 'failed', error = ?, completed_at = ? WHERE id = ?",
                (str(error), utc_now(), job_id),
            )
        raise HTTPException(status_code=502, detail=f"AI 服务调用失败：{error}") from error


@app.get("/api/ai/jobs/{job_id}", response_model=AIJobResponse)
def get_ai_job(job_id: str) -> AIJobResponse:
    job = fetch_one("SELECT * FROM ai_jobs WHERE id = ?", (job_id,))
    if not job:
        raise HTTPException(status_code=404, detail="AI 任务不存在")
    return AIJobResponse(
        id=job["id"],
        skill_key=job["skill_key"],
        skill_version=job["skill_version"],
        provider=job["provider"],
        status=job["status"],
        output=json.loads(job["output_json"]) if job["output_json"] else None,
        error=job["error"],
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(settings.frontend_path / "index.html")


app.mount("/", StaticFiles(directory=settings.frontend_path, html=True), name="frontend")
