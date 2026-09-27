"""Student-style identity entry for teacher workspaces.

This is a convenience gate, not proof of identity: school, subject and name are
known facts. Do not expose this deployment to untrusted users without real auth.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import secrets
import sqlite3
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .database import database, fetch_all, fetch_one, teacher_identity_key, utc_now


router = APIRouter(prefix="/api/teacher-auth")
COOKIE_NAME = "sijie_teacher_identity_session"
SESSION_HOURS = 12


class TeacherIdentity(BaseModel):
    school_name: str = Field(min_length=1, max_length=120)
    subject: str = Field(min_length=1, max_length=50)
    teacher_name: str = Field(min_length=1, max_length=50)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def login_options() -> dict:
    schools = [row["school_name"] for row in fetch_all(
        "SELECT school_name FROM teacher_workspaces UNION SELECT school_name FROM classroom_schools ORDER BY school_name"
    )]
    subjects = [row["subject"] for row in fetch_all(
        "SELECT subject FROM teacher_workspaces UNION SELECT subject FROM precision_teachings ORDER BY subject"
    )]
    return {"schools": schools, "subjects": subjects}


def current_teacher(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    return fetch_one(
        """SELECT w.id AS workspace_id, w.school_name, w.subject, w.teacher_name
           FROM teacher_identity_sessions s JOIN teacher_workspaces w ON w.id = s.workspace_id
           WHERE s.token_hash = ? AND s.expires_at > ?""",
        (_token_hash(token), utc_now()),
    )


def _issue_session(response: Response, request: Request, identity: dict) -> None:
    token = secrets.token_urlsafe(48)
    now = datetime.now(UTC)
    with database() as connection:
        connection.execute("DELETE FROM teacher_identity_sessions WHERE expires_at <= ?", (now.isoformat(),))
        connection.execute(
            """INSERT INTO teacher_identity_sessions
               (token_hash, workspace_id, school_name, subject, teacher_name, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (_token_hash(token), identity["workspace_id"], identity["school_name"], identity["subject"],
             identity["teacher_name"], now.isoformat(), (now + timedelta(hours=SESSION_HOURS)).isoformat()),
        )
    response.set_cookie(
        COOKIE_NAME, token, max_age=SESSION_HOURS * 3600, path="/api",
        httponly=True, secure=request.url.scheme == "https", samesite="strict",
    )
    response.headers["Cache-Control"] = "no-store"


@router.get("/status")
def status(request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    teacher = current_teacher(request)
    return {"authenticated": teacher is not None, "teacher": teacher}


@router.get("/login-options")
def get_login_options(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return login_options()


@router.post("/login")
def login(payload: TeacherIdentity, request: Request, response: Response) -> dict:
    school, subject, name = (payload.school_name.strip(), payload.subject.strip(), payload.teacher_name.strip())
    if not all((school, subject, name)):
        raise HTTPException(status_code=422, detail="请填写完整的学校、学科和教师姓名")
    key = teacher_identity_key(school, subject, name)
    identity = fetch_one("SELECT id AS workspace_id, school_name, subject, teacher_name FROM teacher_workspaces WHERE identity_key = ?", (key,))
    if identity is None:
        workspace_id = f"ws-{uuid4().hex}"
        now = utc_now()
        try:
            with database() as connection:
                connection.execute(
                    """INSERT INTO teacher_workspaces
                       (id, school_name, subject, teacher_name, identity_key, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (workspace_id, school, subject, name, key, now, now),
                )
        except sqlite3.IntegrityError:
            pass  # A concurrent first login created the same space.
        identity = fetch_one("SELECT id AS workspace_id, school_name, subject, teacher_name FROM teacher_workspaces WHERE identity_key = ?", (key,))
    _issue_session(response, request, identity)
    return {"authenticated": True, "teacher": identity}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        with database() as connection:
            connection.execute("DELETE FROM teacher_identity_sessions WHERE token_hash = ?", (_token_hash(token),))
    response.delete_cookie(COOKIE_NAME, path="/api")
    response.headers["Cache-Control"] = "no-store"
    return {"authenticated": False}
