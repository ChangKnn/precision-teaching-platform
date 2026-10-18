"""Account lifecycle management and read-only teaching oversight for administrators."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .database import database, fetch_all, fetch_one, utc_now, write_audit
from .teacher_auth import hash_password, provision_teacher, require_admin

router = APIRouter(prefix="/api/admin")


class TeacherCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=10, max_length=128)
    school_name: str = Field(default="", max_length=120)
    subject: str = Field(default="", max_length=50)
    teacher_name: str = Field(default="", max_length=50)
    workspace_id: str | None = Field(default=None, max_length=100)


class AccountState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class PasswordReset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=10, max_length=128)


def _teacher(account_id: str) -> dict:
    account = fetch_one("SELECT id, workspace_id FROM user_accounts WHERE id = ? AND role = 'teacher'", (account_id,))
    if not account:
        raise HTTPException(status_code=404, detail="教师账号不存在")
    return account


@router.get("/overview")
def overview(request: Request) -> dict:
    require_admin(request)
    return {
        "teachers": fetch_one("SELECT COUNT(*) AS n FROM user_accounts WHERE role = 'teacher'")["n"],
        "enabled_teachers": fetch_one("SELECT COUNT(*) AS n FROM user_accounts WHERE role = 'teacher' AND enabled = 1")["n"],
        "teachings": fetch_one("SELECT COUNT(*) AS n FROM precision_teachings")["n"],
        "submitted_sessions": fetch_one("SELECT COUNT(*) AS n FROM student_task_sessions WHERE status = 'submitted'")["n"],
    }


@router.get("/teachers")
def list_teachers(request: Request) -> dict:
    require_admin(request)
    accounts = fetch_all(
        """SELECT a.id, a.username, a.enabled, a.must_change_password, a.created_at, a.last_login_at,
                  a.workspace_id, w.school_name, w.subject, w.teacher_name,
                  (SELECT COUNT(*) FROM precision_teachings p WHERE p.workspace_id = w.id) AS teaching_count
           FROM user_accounts a JOIN teacher_workspaces w ON w.id = a.workspace_id
           WHERE a.role = 'teacher' ORDER BY a.created_at DESC""")
    unbound = fetch_all(
        """SELECT w.id, w.school_name, w.subject, w.teacher_name,
                  (SELECT COUNT(*) FROM precision_teachings p WHERE p.workspace_id = w.id) AS teaching_count
           FROM teacher_workspaces w WHERE NOT EXISTS (SELECT 1 FROM user_accounts a WHERE a.workspace_id = w.id)
           ORDER BY w.updated_at DESC""")
    return {"teachers": accounts, "unbound_workspaces": unbound}


@router.post("/teachers", status_code=201)
def create_teacher(payload: TeacherCreate, request: Request) -> dict:
    admin = require_admin(request)
    created = provision_teacher(**payload.model_dump())
    write_audit("admin_create_teacher", "user_account", created["id"],
                {"actor": admin["account_id"], "username": created["username"], "workspace_id": created["workspace_id"]})
    return created


@router.patch("/teachers/{account_id}")
def set_teacher_state(account_id: str, payload: AccountState, request: Request) -> dict:
    admin = require_admin(request)
    _teacher(account_id)
    with database() as connection:
        connection.execute("""UPDATE user_accounts SET enabled = ?, session_version = session_version + 1,
                              updated_at = ? WHERE id = ? AND role = 'teacher'""",
                           (int(payload.enabled), utc_now(), account_id))
        connection.execute("DELETE FROM account_sessions WHERE account_id = ?", (account_id,))
    write_audit("admin_enable_teacher" if payload.enabled else "admin_disable_teacher", "user_account", account_id,
                {"actor": admin["account_id"]})
    return {"id": account_id, "enabled": payload.enabled}


@router.post("/teachers/{account_id}/password")
def reset_teacher_password(account_id: str, payload: PasswordReset, request: Request) -> dict:
    admin = require_admin(request)
    _teacher(account_id)
    encoded = hash_password(payload.password)
    with database() as connection:
        connection.execute("""UPDATE user_accounts SET password_hash = ?, must_change_password = 1,
                              session_version = session_version + 1, updated_at = ? WHERE id = ? AND role = 'teacher'""",
                           (encoded, utc_now(), account_id))
        connection.execute("DELETE FROM account_sessions WHERE account_id = ?", (account_id,))
        # Reset account-level lockout; retain per-IP throttling.
        account = connection.execute("SELECT username FROM user_accounts WHERE id = ?", (account_id,)).fetchone()
        from .teacher_auth import _token_hash
        connection.execute("DELETE FROM account_login_attempts WHERE bucket = ?", ("user:" + _token_hash(account["username"]),))
    write_audit("admin_reset_password", "user_account", account_id, {"actor": admin["account_id"]})
    return {"reset": True, "must_change_password": True}


@router.get("/teachings")
def list_teachings(request: Request, workspace_id: str | None = None, q: str = "", limit: int = 50, offset: int = 0) -> dict:
    require_admin(request)
    clauses, params = [], []
    if workspace_id:
        clauses.append("p.workspace_id = ?")
        params.append(workspace_id)
    if q.strip():
        clauses.append("(p.title LIKE ? OR w.teacher_name LIKE ? OR w.school_name LIKE ?)")
        params.extend(["%" + q.strip()[:120] + "%"] * 3)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    total = fetch_one(f"SELECT COUNT(*) AS n FROM precision_teachings p LEFT JOIN teacher_workspaces w ON w.id = p.workspace_id {where}", tuple(params))["n"]
    rows = fetch_all(
        f"""SELECT p.id, p.title, p.status, p.current_stage, p.subject, p.grade, p.workspace_id,
                   p.created_at, p.updated_at, w.teacher_name, w.school_name, a.username,
                   COALESCE(d.status, 'draft') AS diagnosis_status,
                   COALESCE(f.status, 'pending') AS feedback_status,
                   COALESCE(i.status, 'draft') AS intervention_status,
                   (SELECT COUNT(*) FROM student_task_sessions s WHERE s.teaching_id = p.id) AS session_count,
                   (SELECT COUNT(*) FROM student_task_sessions s WHERE s.teaching_id = p.id AND s.status = 'submitted') AS submitted_count
            FROM precision_teachings p LEFT JOIN teacher_workspaces w ON w.id = p.workspace_id
            LEFT JOIN user_accounts a ON a.workspace_id = w.id AND a.role = 'teacher'
            LEFT JOIN diagnosis_tasks d ON d.teaching_id = p.id
            LEFT JOIN feedback_summaries f ON f.teaching_id = p.id
            LEFT JOIN intervention_plans i ON i.teaching_id = p.id
            {where} ORDER BY p.updated_at DESC LIMIT ? OFFSET ?""",
        (*params, max(1, min(limit, 100)), max(0, offset)))
    return {"teachings": rows, "total": total}


@router.get("/teachings/{teaching_id}")
def teaching_detail(teaching_id: str, request: Request) -> dict:
    admin = require_admin(request)
    teaching = fetch_one(
        """SELECT p.*, w.school_name, w.teacher_name FROM precision_teachings p
           LEFT JOIN teacher_workspaces w ON w.id = p.workspace_id WHERE p.id = ?""", (teaching_id,))
    if not teaching:
        raise HTTPException(status_code=404, detail="精准教学不存在")
    diagnosis = fetch_one("SELECT diagnosis_type, goal, task_text, ai_role, duration_minutes, status, updated_at FROM diagnosis_tasks WHERE teaching_id = ?", (teaching_id,))
    classrooms = fetch_all(
        """SELECT c.name, a.is_published FROM diagnosis_assignments a
           JOIN classrooms c ON c.id = a.classroom_id WHERE a.teaching_id = ? ORDER BY c.name""", (teaching_id,))
    write_audit("admin_view_teaching", "precision_teaching", teaching_id, {"actor": admin["account_id"]})
    return {"teaching": teaching, "diagnosis": diagnosis, "classrooms": classrooms}
