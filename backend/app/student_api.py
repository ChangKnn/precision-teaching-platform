from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
import unicodedata
import jsonschema
from urllib.parse import unquote
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse

from .config import settings
from .custom_analysis_api import custom_report_for, standard_for
from .database import database, fetch_all, fetch_one, utc_now, write_audit
from .schemas import StudentDiagnosisReportUpdate, StudentLogin, StudentMessageCreate, StudentTaskSubmit
from .services.ai import run_student_diagnosis_report
from .services.class_report import queue_class_report
from .services.skills import load_skill
from .services.student_chat import generate_student_reply
from .services.student_dialogue_skill import freeze_dialogue_config, generate_dialogue_turn, initial_skill_message


router = APIRouter(prefix="/api")


def normalize_identity(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\s()（）·._-]+", "", normalized)


def login_options() -> list[dict]:
    rows = fetch_all(
        """
        SELECT DISTINCT cs.school_name, c.id AS classroom_id, c.name AS class_name
        FROM diagnosis_assignments da
        JOIN diagnosis_tasks d ON d.teaching_id = da.teaching_id AND d.status = 'published'
        JOIN precision_teachings p ON p.id = da.teaching_id
        JOIN teacher_workspaces tw ON tw.id = p.workspace_id
        JOIN classrooms c ON c.id = da.classroom_id
        JOIN classroom_schools cs ON cs.classroom_id = c.id AND cs.school_name = tw.school_name
        WHERE da.is_published = 1
        ORDER BY cs.school_name, c.name
        """
    )
    schools: dict[str, list[dict]] = {}
    for row in rows:
        schools.setdefault(row["school_name"], []).append(
            {"id": row["classroom_id"], "name": row["class_name"]}
        )
    return [{"name": name, "classes": classes} for name, classes in schools.items()]


def match_classroom(school_name: str, class_name: str) -> dict | None:
    rows = fetch_all(
        """
        SELECT c.*, cs.school_name
        FROM classrooms c
        JOIN classroom_schools cs ON cs.classroom_id = c.id
        WHERE EXISTS (
            SELECT 1 FROM diagnosis_assignments da
            JOIN diagnosis_tasks d ON d.teaching_id = da.teaching_id AND d.status = 'published'
            JOIN precision_teachings p ON p.id = da.teaching_id
            JOIN teacher_workspaces tw ON tw.id = p.workspace_id
            WHERE da.classroom_id = c.id AND da.is_published = 1 AND tw.school_name = cs.school_name
        )
        ORDER BY cs.school_name, c.name
        """
    )
    normalized_school = normalize_identity(school_name)
    normalized_class = normalize_identity(class_name)
    class_matches = [row for row in rows if normalize_identity(row["name"]) == normalized_class]
    school_matches = [
        row for row in class_matches
        if normalized_school == normalize_identity(row["school_name"])
        or normalized_school in normalize_identity(row["school_name"])
        or normalize_identity(row["school_name"]) in normalized_school
    ]
    if len(school_matches) == 1:
        return school_matches[0]
    return None


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


def require_student(x_student_token: str | None = Header(default=None)) -> dict:
    if not x_student_token:
        raise HTTPException(status_code=401, detail="学生登录状态已失效，请重新登录")
    student = fetch_one(
        """
        SELECT s.*
        FROM student_login_sessions ls
        JOIN students s ON s.id = ls.student_id
        WHERE ls.token = ? AND ls.expires_at > ?
        """,
        (x_student_token, utc_now()),
    )
    if not student:
        raise HTTPException(status_code=401, detail="学生登录状态已失效，请重新登录")
    return student


def task_rows(student: dict) -> list[dict]:
    return fetch_all(
        """
        SELECT p.id AS teaching_id, p.title, p.subject, p.grade, p.textbook,
               d.diagnosis_type, d.task_text, d.duration_minutes, d.updated_at,
               tw.teacher_name AS teacher_name,
               sts.id AS session_id, sts.status AS session_status,
               sts.started_at, sts.submitted_at,
               sdr.status AS report_status, sdr.pushed_at AS report_pushed_at
        FROM diagnosis_assignments da
        JOIN precision_teachings p ON p.id = da.teaching_id
        JOIN diagnosis_tasks d ON d.teaching_id = p.id AND d.status = 'published'
        JOIN teacher_workspaces tw ON tw.id = p.workspace_id
        LEFT JOIN student_task_sessions sts
          ON sts.teaching_id = p.id AND sts.student_id = ?
        LEFT JOIN student_diagnosis_reports sdr
          ON sdr.session_id = sts.id AND sdr.status = 'pushed'
        WHERE da.classroom_id = ? AND da.is_published = 1 AND tw.school_name = ?
        ORDER BY CASE WHEN sts.status = 'in_progress' THEN 0
                      WHEN sts.status IS NULL THEN 1 ELSE 2 END,
                 d.updated_at DESC
        """,
        (student["id"], student["classroom_id"], student["school_name"]),
    )


def bootstrap_payload(student: dict) -> dict:
    return {
        "student": {
            "id": student["id"],
            "name": student["name"],
            "school_name": student["school_name"],
            "class_name": student["class_name"],
        },
        "tasks": task_rows(student),
        "ai_provider": settings.student_ai_provider,
    }


def owned_session(session_id: str, student_id: str) -> dict:
    session = fetch_one(
        """
        SELECT sts.*, d.task_text, d.ai_role, d.duration_minutes,
               p.title, p.subject, tw.teacher_name AS teacher_name
        FROM student_task_sessions sts
        JOIN diagnosis_tasks d ON d.teaching_id = sts.teaching_id
        JOIN precision_teachings p ON p.id = sts.teaching_id
        JOIN teacher_workspaces tw ON tw.id = p.workspace_id
        WHERE sts.id = ? AND sts.student_id = ?
        """,
        (session_id, student_id),
    )
    if not session:
        raise HTTPException(status_code=404, detail="学习会话不存在")
    return session


def serialize_session(session: dict) -> dict:
    messages = fetch_all(
        """
        SELECT id, role, content, provider, created_at
        FROM student_messages WHERE session_id = ? ORDER BY id
        """,
        (session["id"],),
    )
    submissions = fetch_all(
        """
        SELECT id, original_filename, content_type, size_bytes, created_at
        FROM student_submissions WHERE session_id = ? ORDER BY created_at
        """,
        (session["id"],),
    )
    pushed_report = fetch_one(
        """
        SELECT session_id, skill_key, skill_version, provider, status,
               generated_json, student_feedback_text, pushed_at, updated_at
        FROM student_diagnosis_reports
        WHERE session_id = ? AND status = 'pushed'
        """,
        (session["id"],),
    )
    return {
        "id": session["id"],
        "teaching_id": session["teaching_id"],
        "status": session["status"],
        "started_at": session["started_at"],
        "submitted_at": session["submitted_at"],
        "submitted_text": session.get("submitted_text"),
        "messages": messages,
        "submissions": submissions,
        "report": (
            {
                **{
                    key: pushed_report[key]
                    for key in ("session_id", "skill_key", "skill_version", "provider", "status", "pushed_at", "updated_at")
                },
                "report_text": student_feedback_for_row(pushed_report),
            }
            if pushed_report else None
        ),
    }


def student_feedback_for_row(report: dict) -> str:
    saved = (report.get("student_feedback_text") or "").strip()
    if saved:
        return saved
    # Earlier reports predate a separately generated student version. Present
    # their existing diagnosis in student-facing language without a model call.
    result = json.loads(report["generated_json"])
    diagnosis = result.get("diagnosis") or {}
    progression = result.get("progression") or {}

    def student_language(value: str) -> str:
        text = (value or "").strip()
        for before, after in (
            ("判为前结构", "说明这次还没有找到与问题直接相关的解题起点"),
            ("符合前结构表现", "说明这次还没有找到与问题直接相关的解题起点"),
            ("符合前结构", "说明这次还没有找到与问题直接相关的解题起点"),
            ("达到单点结构", "说明你已经抓住一个有用的线索"),
            ("符合多点结构", "说明你找到了多个线索，但还需把它们连起来"),
            ("达到多点结构", "说明你找到了多个不同的有用信息"),
            ("支持关联结构判断", "也说明你已经形成较完整的解题思路"),
            ("完整支持本题内的关联结构", "说明你在本题中形成了较完整的解题思路"),
            ("达到本任务的关联结构层级", "说明你已经把线索连成本题需要的完整思路"),
            ("达到本任务的关联结构", "说明你已经把线索连成本题需要的完整思路"),
            ("达到本题的关联结构", "说明你已经把线索连成本题需要的完整思路"),
            ("达到多点结构的可观察表现", "说明你找到了多个不同的有用信息"),
            ("证据支持U而非多个要素及其关系", "这说明你找到一个有用线索，但还没有把更多信息连起来"),
            ("不能据此判断学生缺乏EA能力", "不能据此认为你无法在新情境中举一反三"),
            ("不能据此判断你缺乏EA能力", "不能据此认为你无法在新情境中举一反三"),
            ("因此综合判为R", "因此这次已形成较完整的解题思路"),
            ("符合 EA", "说明你已把方法推广到新的条件"),
            ("足以达到 U", "说明你已找到一个与问题直接相关的线索"),
            ("量规所要求的", "更进一步的"),
            ("有效认知点", "有用线索"),
            ("拓展抽象结构", "在新情境中概括或运用方法"),
            ("关联结构", "连贯的解题思路"),
            ("多点结构", "多个线索尚未连成完整思路"),
            ("单点结构", "一个有效线索"),
            ("前结构", "尚未找到有效起点"),
        ):
            text = text.replace(before, after)
        for before, after in (
            ("学生自主", "你自己"), ("学生已经", "你已经"), ("学生能够", "你能够"),
            ("学生", "你"), ("当前材料", "这次作答"), ("现有材料", "这次作答"),
            ("提交文本", "提交的文字"), ("本任务", "这道题"), ("当前任务", "这道题"),
            ("未观察到", "这次还没有看到"), ("尚未展示", "这次还没有展示"),
            ("完成表现为", "做完后可以检查："), ("完成表现是", "做完后可以检查："),
            ("完成表现：", "做完后可以检查："), ("主要障碍是", "当前最需要突破的是"),
            ("认知要素", "有用的信息"), ("认知点", "有用的线索"),
            ("认知活动", "思考过程"), ("层级", "表现"),
        ):
            text = text.replace(before, after)
        text = re.sub(r"(?<![A-Za-z])(?:P|U|M|R|EA)（[^）]*）", "下一步", text)
        text = re.sub(r"(?<![A-Za-z])(?:P|U|M|R|EA)(?=\s*(?:表现|判断|要求))", "相应", text)
        return text

    summary = student_language(result.get("thinking_structure_summary") or "")
    for before, after in (
        ("信息识别：", "你找到了什么："), ("关系建构：", "你怎样把信息连起来："),
        ("整体组织：", "你的解题过程："), ("抽象迁移：", "换个情境再想想："),
    ):
        summary = summary.replace(before, f"\n- **{after}**")
    evidence_lines = []
    for item in (diagnosis.get("evidence") or [])[:2]:
        quote = (item.get("quote") or "").strip()
        interpretation = student_language(item.get("interpretation") or "")
        if quote:
            evidence_lines.append(f"- 你写道：“{quote}”\n  这说明：{interpretation}")
    strategy_lines = [
        f"{index}. **{student_language(item.get('title') or '练习建议')}**：{student_language(item.get('action') or '')}"
        for index, item in enumerate((result.get("strategies") or [])[:3], start=1)
    ]
    return (
        f"### 这次的学习表现\n{student_language(diagnosis.get('rationale') or '')}\n\n"
        f"### 你的作答告诉了我们什么\n{chr(10).join(evidence_lines) or '请和老师一起核对本次作答中的关键依据。'}\n\n"
        f"### 你现在的思路\n{summary}\n\n"
        f"### 接下来要突破什么\n{student_language(progression.get('main_obstacle') or diagnosis.get('next_level_gap') or '')}\n\n"
        f"### 下一步目标\n{student_language(progression.get('concrete_goal') or '')}\n\n"
        f"### 可以怎样练\n{chr(10).join(strategy_lines) or '请和老师一起确定下一步练习。'}"
    )


def serialize_student_report(session_id: str) -> dict | None:
    report = fetch_one("SELECT * FROM student_diagnosis_reports WHERE session_id = ?", (session_id,))
    if not report:
        return None
    return {
        "session_id": report["session_id"],
        "skill_key": report["skill_key"],
        "skill_version": report["skill_version"],
        "provider": report["provider"],
        "status": report["status"],
        "result": json.loads(report["generated_json"]),
        "report_text": report["report_text"],
        "student_feedback_text": student_feedback_for_row(report),
        "generated_at": report["generated_at"],
        "reviewed_at": report["reviewed_at"],
        "pushed_at": report["pushed_at"],
        "updated_at": report["updated_at"],
    }


@router.post("/student/login")
def login_student(payload: StudentLogin) -> dict:
    school_name = payload.school_name.strip()
    class_name = payload.class_name.strip()
    name = payload.name.strip()
    classroom = match_classroom(school_name, class_name)
    if not classroom:
        available = "；".join(
            f"{school['name']}：{'、'.join(item['name'] for item in school['classes'])}"
            for school in login_options()
        )
        raise HTTPException(
            status_code=404,
            detail=f"该班级暂无已发布任务。当前可选：{available}" if available else "老师尚未发布可登录的诊断任务",
        )
    school_name = classroom["school_name"]
    class_name = classroom["name"]

    now = utc_now()
    student = fetch_one(
        "SELECT * FROM students WHERE school_name = ? AND class_name = ? AND name = ?",
        (school_name, class_name, name),
    )
    if not student:
        student_id = f"stu-{uuid4().hex[:16]}"
        with database() as connection:
            connection.execute(
                """
                INSERT INTO students
                (id, classroom_id, school_name, class_name, name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (student_id, classroom["id"], school_name, class_name, name, now, now),
            )
        student = fetch_one("SELECT * FROM students WHERE id = ?", (student_id,))

    token = uuid4().hex + uuid4().hex
    expires_at = (datetime.now(UTC) + timedelta(hours=12)).isoformat()
    with database() as connection:
        connection.execute("DELETE FROM student_login_sessions WHERE expires_at <= ?", (now,))
        connection.execute(
            """
            INSERT INTO student_login_sessions (token, student_id, created_at, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (token, student["id"], now, expires_at),
        )
    return {"token": token, **bootstrap_payload(student)}


@router.get("/student/login-options")
def get_login_options() -> dict:
    return {"schools": login_options()}


@router.get("/student/bootstrap")
def student_bootstrap(student: dict = Depends(require_student)) -> dict:
    return bootstrap_payload(student)


@router.delete("/student/session")
def logout_student(
    student: dict = Depends(require_student),
    x_student_token: str | None = Header(default=None),
) -> dict:
    with database() as connection:
        connection.execute(
            "DELETE FROM student_login_sessions WHERE token = ? AND student_id = ?",
            (x_student_token, student["id"]),
        )
    return {"status": "logged_out"}


@router.post("/student/tasks/{teaching_id}/session")
def start_task(teaching_id: str, student: dict = Depends(require_student)) -> dict:
    task = fetch_one(
        """
        SELECT p.id AS teaching_id, d.task_text, d.duration_minutes,
               p.title, p.subject, tw.teacher_name AS teacher_name
        FROM diagnosis_assignments da
        JOIN precision_teachings p ON p.id = da.teaching_id
        JOIN diagnosis_tasks d ON d.teaching_id = p.id AND d.status = 'published'
        JOIN teacher_workspaces tw ON tw.id = p.workspace_id
        WHERE da.teaching_id = ? AND da.classroom_id = ? AND da.is_published = 1 AND tw.school_name = ?
        """,
        (teaching_id, student["classroom_id"], student["school_name"]),
    )
    if not task:
        raise HTTPException(status_code=404, detail="诊断任务不存在或未分配到当前班级")
    session = fetch_one(
        "SELECT * FROM student_task_sessions WHERE student_id = ? AND teaching_id = ?",
        (student["id"], teaching_id),
    )
    now = utc_now()
    if not session:
        session_id = f"ses-{uuid4().hex[:20]}"
        try:
            dialogue_config = freeze_dialogue_config(teaching_id)
            opening, execution = initial_skill_message(dialogue_config, session_id, student["id"])
        except (ValueError, RuntimeError, jsonschema.ValidationError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        with database() as connection:
            connection.execute(
                """
                INSERT INTO student_task_sessions
                (id, student_id, teaching_id, status, started_at, last_active_at, dialogue_config_json)
                VALUES (?, ?, ?, 'in_progress', ?, ?, ?)
                """,
                (session_id, student["id"], teaching_id, now, now, json.dumps(dialogue_config, ensure_ascii=False)),
            )
            connection.execute(
                """
                INSERT INTO student_messages
                (session_id, role, content, provider, execution_json, created_at)
                VALUES (?, 'assistant', ?, 'local-skill', ?, ?)
                """,
                (session_id, opening, json.dumps(execution, ensure_ascii=False), now),
            )
        session = fetch_one("SELECT * FROM student_task_sessions WHERE id = ?", (session_id,))
    return {"task": task, "session": serialize_session(session)}


@router.get("/student/sessions/{session_id}")
def get_student_session(session_id: str, student: dict = Depends(require_student)) -> dict:
    return serialize_session(owned_session(session_id, student["id"]))


@router.post("/student/sessions/{session_id}/messages")
def create_message(
    session_id: str,
    payload: StudentMessageCreate,
    student: dict = Depends(require_student),
) -> dict:
    session = owned_session(session_id, student["id"])
    pushed_report = fetch_one(
        """
        SELECT report_text, pushed_at
        FROM student_diagnosis_reports
        WHERE session_id = ? AND status = 'pushed'
        """,
        (session_id,),
    )
    if session["status"] == "submitted" and not pushed_report:
        raise HTTPException(status_code=409, detail="该任务已经提交；老师推送反馈报告后可以继续复盘对话")
    student_text = payload.content
    if not student_text.strip():
        raise HTTPException(status_code=422, detail="请先输入对话内容")
    if not pushed_report:
        try:
            config = json.loads(session["dialogue_config_json"]) if session.get("dialogue_config_json") else freeze_dialogue_config(session["teaching_id"])
            history = fetch_all(
                "SELECT id, role, content FROM student_messages WHERE session_id = ? ORDER BY id",
                (session_id,),
            )
            result, provider, response_id = generate_dialogue_turn(config, session_id, student["id"], history, student_text)
        except (ValueError, RuntimeError, jsonschema.ValidationError) as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        now = utc_now()
        reply_time = utc_now()
        with database() as connection:
            if not session.get("dialogue_config_json"):
                connection.execute(
                    "UPDATE student_task_sessions SET dialogue_config_json = ? WHERE id = ?",
                    (json.dumps(config, ensure_ascii=False), session_id),
                )
            cursor = connection.execute(
                "INSERT INTO student_messages (session_id, role, content, provider, created_at) VALUES (?, 'student', ?, 'local', ?)",
                (session_id, student_text, now),
            )
            student_message_id = cursor.lastrowid
            cursor = connection.execute(
                """INSERT INTO student_messages
                   (session_id, role, content, provider, external_response_id, execution_json, created_at)
                   VALUES (?, 'assistant', ?, ?, ?, ?, ?)""",
                (session_id, result["student_visible_reply"], provider, response_id,
                 json.dumps(result["internal_execution_record"], ensure_ascii=False), reply_time),
            )
            assistant_message_id = cursor.lastrowid
            connection.execute("UPDATE student_task_sessions SET last_active_at = ? WHERE id = ?", (reply_time, session_id))
        return {
            "student_message": {"id": student_message_id, "role": "student", "content": student_text, "provider": "local", "created_at": now},
            "assistant_message": {"id": assistant_message_id, "role": "assistant", "content": result["student_visible_reply"], "provider": provider, "created_at": reply_time},
        }
    now = utc_now()
    with database() as connection:
        cursor = connection.execute(
            """
            INSERT INTO student_messages (session_id, role, content, provider, created_at)
            VALUES (?, 'student', ?, 'local', ?)
            """,
            (session_id, student_text, now),
        )
        student_message_id = cursor.lastrowid
        connection.execute(
            "UPDATE student_task_sessions SET last_active_at = ? WHERE id = ?",
            (now, session_id),
        )

    history = fetch_all(
        "SELECT role, content FROM student_messages WHERE session_id = ? ORDER BY id",
        (session_id,),
    )
    try:
        reply, provider, response_id = generate_student_reply(
            session,
            history,
            student["id"],
            pushed_report["report_text"] if pushed_report else None,
        )
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    reply_time = utc_now()
    with database() as connection:
        cursor = connection.execute(
            """
            INSERT INTO student_messages
            (session_id, role, content, provider, external_response_id, created_at)
            VALUES (?, 'assistant', ?, ?, ?, ?)
            """,
            (session_id, reply, provider, response_id, reply_time),
        )
        assistant_message_id = cursor.lastrowid
    return {
        "student_message": {
            "id": student_message_id,
            "role": "student",
            "content": student_text,
            "provider": "local",
            "created_at": now,
        },
        "assistant_message": {
            "id": assistant_message_id,
            "role": "assistant",
            "content": reply,
            "provider": provider,
            "created_at": reply_time,
        },
    }


@router.post("/student/sessions/{session_id}/files", status_code=201)
async def upload_file(
    session_id: str,
    request: Request,
    student: dict = Depends(require_student),
    x_file_name: str | None = Header(default=None),
) -> dict:
    session = owned_session(session_id, student["id"])
    if session["status"] == "submitted":
        raise HTTPException(status_code=409, detail="该任务已经提交")
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    extensions = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
    if content_type not in extensions:
        raise HTTPException(status_code=415, detail="仅支持 JPG、PNG 和 WEBP 图片")
    content = await request.body()
    if not content or len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="单张图片应小于 10 MB")

    original_filename = Path(unquote(x_file_name or "student-work")).name[:180]
    file_id = f"file-{uuid4().hex[:20]}"
    stored_filename = f"{file_id}{extensions[content_type]}"
    (settings.upload_path / stored_filename).write_bytes(content)
    now = utc_now()
    with database() as connection:
        connection.execute(
            """
            INSERT INTO student_submissions
            (id, session_id, original_filename, stored_filename, content_type, size_bytes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (file_id, session_id, original_filename, stored_filename, content_type, len(content), now),
        )
    return {
        "id": file_id,
        "original_filename": original_filename,
        "content_type": content_type,
        "size_bytes": len(content),
        "created_at": now,
    }


@router.post("/student/sessions/{session_id}/submit")
def submit_task(
    session_id: str,
    payload: StudentTaskSubmit,
    student: dict = Depends(require_student),
) -> dict:
    session = owned_session(session_id, student["id"])
    if session["status"] == "submitted":
        raise HTTPException(status_code=409, detail="该任务已经提交")
    submitted_text = payload.submitted_text.strip()
    now = utc_now()
    with database() as connection:
        connection.execute(
            """
            UPDATE student_task_sessions
            SET status = 'submitted', submitted_text = ?, submitted_at = ?, last_active_at = ?
            WHERE id = ?
            """,
            (submitted_text, now, now, session_id),
        )
        connection.execute("DELETE FROM student_diagnosis_reports WHERE session_id = ?", (session_id,))
    refresh_feedback_counts(session["teaching_id"])
    write_audit("submit", "student_task_session", session_id, {
        "student_id": student["id"], "submission_type": "text" if submitted_text else "dialogue_only",
    })
    return serialize_session(owned_session(session_id, student["id"]))


@router.get("/precision-teachings/{teaching_id}/student-results")
def student_results(teaching_id: str, background_tasks: BackgroundTasks) -> dict:
    teaching = fetch_one("SELECT id, title FROM precision_teachings WHERE id = ?", (teaching_id,))
    if not teaching:
        raise HTTPException(status_code=404, detail="精准教学不存在")
    rows = fetch_all(
        """
        SELECT sts.*, s.name AS student_name, s.class_name, s.school_name, s.classroom_id
        FROM student_task_sessions sts
        JOIN students s ON s.id = sts.student_id
        JOIN diagnosis_assignments da ON da.teaching_id = sts.teaching_id
          AND da.classroom_id = s.classroom_id AND da.is_published = 1
        WHERE sts.teaching_id = ? AND sts.status = 'submitted'
        ORDER BY sts.submitted_at DESC
        """,
        (teaching_id,),
    )
    custom_standard = standard_for(teaching_id)
    results = []
    for row in rows:
        detail = serialize_session(row)
        for submission in detail["submissions"]:
            submission["file_url"] = f"/api/teacher/submissions/{submission['id']}/file"
        detail["student"] = {
            "id": row["student_id"],
            "name": row["student_name"],
            "class_name": row["class_name"],
            "school_name": row["school_name"],
            "classroom_id": row["classroom_id"],
        }
        detail["report"] = serialize_student_report(row["id"])
        detail["custom_analysis"] = (
            custom_report_for(teaching_id, "individual", row["id"])
            if custom_standard["individual_enabled"] else None
        )
        results.append(detail)
    classrooms = fetch_all(
        """
        SELECT c.id, c.name FROM diagnosis_assignments da
        JOIN classrooms c ON c.id = da.classroom_id
        WHERE da.teaching_id = ? AND da.is_published = 1 ORDER BY c.name
        """,
        (teaching_id,),
    )
    class_reports = {}
    for classroom in classrooms:
        class_reports[classroom["id"]] = queue_class_report(teaching_id, classroom["id"], background_tasks)
    custom_class_reports = {
        classroom["id"]: custom_report_for(teaching_id, "class", classroom["id"])
        for classroom in classrooms
    } if custom_standard["class_enabled"] else {}
    return {"teaching": teaching, "results": results, "submitted_count": len(results),
            "classrooms": classrooms, "class_reports": class_reports,
            "custom_analysis_standard": custom_standard, "custom_class_reports": custom_class_reports}


@router.post("/precision-teachings/{teaching_id}/classrooms/{classroom_id}/class-report/generate")
def regenerate_class_report(teaching_id: str, classroom_id: str, background_tasks: BackgroundTasks) -> dict:
    assignment = fetch_one(
        "SELECT 1 FROM diagnosis_assignments WHERE teaching_id = ? AND classroom_id = ? AND is_published = 1",
        (teaching_id, classroom_id),
    )
    if not assignment:
        raise HTTPException(status_code=404, detail="该精准教学未发布到所选班级")
    report = queue_class_report(teaching_id, classroom_id, background_tasks, force=True)
    if not report:
        raise HTTPException(status_code=409, detail="该班级还没有可用的学生个体报告")
    return {"report": report}


def build_student_diagnosis_input(session_id: str) -> tuple[dict, dict]:
    context = fetch_one(
        """
        SELECT sts.*, s.name AS student_name,
               p.subject, p.grade, p.textbook, p.title, p.goal, p.content,
               d.task_text, dr.status AS rubric_status, dr.current_json
        FROM student_task_sessions sts
        JOIN students s ON s.id = sts.student_id
        JOIN precision_teachings p ON p.id = sts.teaching_id
        JOIN diagnosis_tasks d ON d.teaching_id = sts.teaching_id
        LEFT JOIN diagnosis_rubrics dr ON dr.teaching_id = sts.teaching_id
        WHERE sts.id = ?
        """,
        (session_id,),
    )
    if not context:
        raise HTTPException(status_code=404, detail="学生学习会话不存在")
    if context["status"] != "submitted":
        raise HTTPException(status_code=409, detail="学生尚未提交学习成果")
    if context["rubric_status"] != "confirmed" or not context["current_json"]:
        raise HTTPException(status_code=409, detail="请先确认当前诊断任务的 SOLO 分析标准")
    messages = fetch_all(
        "SELECT id, role, content FROM student_messages WHERE session_id = ? ORDER BY id",
        (session_id,),
    )
    if not any(item["role"] == "student" for item in messages):
        raise HTTPException(status_code=422, detail="至少需要一条学生发言才能生成个体报告")
    contents = [item.strip() for item in context["content"].splitlines() if item.strip()]
    payload = {
        "precision_teaching_context": {
            "subject": context["subject"],
            "grade": context["grade"],
            "textbook_version_and_chapter": context["textbook"],
            "precision_teaching_topic": context["title"],
            "precision_teaching_goals": [context["goal"]],
            "precision_teaching_content": contents or [context["content"]],
        },
        "diagnostic_task": {
            "task_text": context["task_text"],
            "requirements": ["与 AI 对话，完整说明自己的想法和理由", "最终作答文本可选，未填写时仅依据对话记录分析"],
        },
        "task_specific_solo_rubric": json.loads(context["current_json"]),
        "student": {
            "student_id": context["student_id"],
            "student_name": f"匿名学生-{context['student_id'][-6:]}",
        },
        "human_ai_dialogue": [
            {"turn_id": f"turn-{item['id']}", "role": item["role"], "content": item["content"]}
            for item in messages
        ],
        "submitted_text": context["submitted_text"],
    }
    return payload, context


@router.post("/student-results/{session_id}/report/generate")
def generate_student_report(session_id: str, background_tasks: BackgroundTasks) -> dict:
    skill = load_skill("solo_student_diagnosis_feedback")
    payload, context = build_student_diagnosis_input(session_id)
    try:
        import jsonschema

        input_schema = json.loads((skill.path.parent / "assets/diagnosis-input.schema.json").read_text(encoding="utf-8"))
        jsonschema.validate(payload, input_schema)
    except Exception as error:
        raise HTTPException(status_code=422, detail=f"学生报告输入校验失败：{error}") from error
    input_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    input_hash = hashlib.sha256(input_json.encode("utf-8")).hexdigest()
    job_id = f"job-{uuid4().hex}"
    created_at = utc_now()
    with database() as connection:
        connection.execute(
            """
            INSERT INTO ai_jobs
            (id, skill_key, skill_version, provider, status, input_json, created_at)
            VALUES (?, ?, ?, ?, 'running', ?, ?)
            """,
            (job_id, skill.key, skill.version, settings.ai_provider, input_json, created_at),
        )
    try:
        result, report_text, student_feedback_text = run_student_diagnosis_report(skill, payload, settings.ai_provider)
        report_text = report_text.replace(payload["student"]["student_name"], context["student_name"])
        completed_at = utc_now()
        output_json = json.dumps(result, ensure_ascii=False)
        with database() as connection:
            connection.execute(
                "UPDATE ai_jobs SET status = 'completed', output_json = ?, completed_at = ? WHERE id = ?",
                (output_json, completed_at, job_id),
            )
            connection.execute(
                """
                INSERT INTO student_diagnosis_reports
                (session_id, teaching_id, skill_key, skill_version, provider, status,
                 input_hash, generated_json, report_text, student_feedback_text, generated_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    teaching_id = excluded.teaching_id,
                    skill_key = excluded.skill_key,
                    skill_version = excluded.skill_version,
                    provider = excluded.provider,
                    status = 'draft',
                    input_hash = excluded.input_hash,
                    generated_json = excluded.generated_json,
                    report_text = excluded.report_text,
                    student_feedback_text = excluded.student_feedback_text,
                    generated_at = excluded.generated_at,
                    reviewed_at = NULL,
                    pushed_at = NULL,
                    updated_at = excluded.updated_at
                """,
                (session_id, context["teaching_id"], skill.key, skill.version, settings.ai_provider,
                 input_hash, output_json, report_text, student_feedback_text, completed_at, completed_at),
            )
        write_audit("generate", "student_diagnosis_report", session_id, {"job_id": job_id, "skill": skill.key})
        classroom = fetch_one("SELECT classroom_id FROM students WHERE id = ?", (context["student_id"],))
        if classroom:
            try:
                queue_class_report(context["teaching_id"], classroom["classroom_id"], background_tasks)
            except Exception as class_error:
                write_audit("queue_failed", "class_diagnosis_report", context["teaching_id"], {"error": str(class_error)[:500]})
        return {"job_id": job_id, "report": serialize_student_report(session_id)}
    except Exception as error:
        with database() as connection:
            connection.execute(
                "UPDATE ai_jobs SET status = 'failed', error = ?, completed_at = ? WHERE id = ?",
                (str(error), utc_now(), job_id),
            )
        raise HTTPException(status_code=502, detail=f"学生报告生成失败：{error}") from error


@router.put("/student-results/{session_id}/report")
def update_student_report(session_id: str, payload: StudentDiagnosisReportUpdate) -> dict:
    report = fetch_one("SELECT * FROM student_diagnosis_reports WHERE session_id = ?", (session_id,))
    if not report:
        raise HTTPException(status_code=404, detail="请先生成学生个体报告")
    if report["provider"] == "mock" and payload.status != "draft":
        raise HTTPException(status_code=409, detail="Mock AI 报告只能用于预览，配置真实模型并重新生成后才能确认或推送")
    now = utc_now()
    reviewed_at = now if payload.status in {"confirmed", "pushed"} else None
    pushed_at = now if payload.status == "pushed" else None
    with database() as connection:
        connection.execute(
            """
            UPDATE student_diagnosis_reports
            SET report_text = ?, student_feedback_text = ?, status = ?, reviewed_at = ?, pushed_at = ?, updated_at = ?
            WHERE session_id = ?
            """,
            (payload.report_text.strip(),
             payload.student_feedback_text.strip() if payload.student_feedback_text is not None else report["student_feedback_text"],
             payload.status, reviewed_at, pushed_at, now, session_id),
        )
    write_audit(payload.status, "student_diagnosis_report", session_id, {})
    return {"report": serialize_student_report(session_id)}


@router.get("/teacher/submissions/{submission_id}/file")
def submission_file(submission_id: str) -> FileResponse:
    submission = fetch_one("SELECT * FROM student_submissions WHERE id = ?", (submission_id,))
    if not submission:
        raise HTTPException(status_code=404, detail="学生作答图片不存在")
    path = settings.upload_path / submission["stored_filename"]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="学生作答图片文件已丢失")
    return FileResponse(
        path,
        media_type=submission["content_type"],
    )
