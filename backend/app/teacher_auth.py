"""Administrator-provisioned accounts; legacy identity sessions are never trusted."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import re
import secrets
import sqlite3
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from .database import database, fetch_one, teacher_identity_key, utc_now, write_audit

router = APIRouter(prefix="/api/teacher-auth")
admin_auth_router = APIRouter(prefix="/api/admin-auth")
COOKIE_NAME = "sijie_account_session"
SESSION_HOURS = 12
PASSWORD_ITERATIONS = 600_000


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


def normalize_username(value: str) -> str:
    result = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", result):
        raise HTTPException(status_code=422, detail="账号需为3—64位字母、数字、下划线、点或短横线")
    return result


def hash_password(password: str) -> str:
    if not 10 <= len(password) <= 128:
        raise HTTPException(status_code=422, detail="密码长度需为10—128位")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or int(iterations) != PASSWORD_ITERATIONS:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PASSWORD_ITERATIONS).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = hash_password(secrets.token_urlsafe(24))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def current_account(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token or len(token) > 128:
        return None
    return fetch_one(
        """SELECT a.id AS account_id, a.username, a.role, a.workspace_id,
                  a.must_change_password, a.session_version, w.school_name, w.subject, w.teacher_name
           FROM account_sessions s JOIN user_accounts a ON a.id = s.account_id
           LEFT JOIN teacher_workspaces w ON w.id = a.workspace_id
           WHERE s.token_hash = ? AND s.expires_at > ? AND a.enabled = 1 AND s.session_version = a.session_version""",
        (_token_hash(token), utc_now()))


def current_teacher(request: Request) -> dict | None:
    account = current_account(request)
    return account if account and account["role"] == "teacher" else None


def require_admin(request: Request) -> dict:
    account = current_account(request)
    if not account:
        raise HTTPException(status_code=401, detail="请先登录管理员账号")
    if account["role"] != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可执行此操作")
    if account["must_change_password"]:
        raise HTTPException(status_code=403, detail="请先修改初始密码")
    return account


def public_account(account: dict) -> dict:
    return {key: account.get(key) for key in
            ("account_id", "username", "role", "workspace_id", "school_name", "subject", "teacher_name", "must_change_password")}


def _issue_session(response: Response, request: Request, identity: dict) -> None:
    token = secrets.token_urlsafe(48)
    now = datetime.now(UTC)
    with database() as connection:
        connection.execute("DELETE FROM account_sessions WHERE expires_at <= ?", (now.isoformat(),))
        created = connection.execute(
            """INSERT INTO account_sessions (token_hash, account_id, session_version, created_at, expires_at)
               SELECT ?, id, session_version, ?, ? FROM user_accounts
               WHERE id = ? AND enabled = 1 AND session_version = ?""",
            (_token_hash(token), now.isoformat(), (now + timedelta(hours=SESSION_HOURS)).isoformat(),
             identity["account_id"], identity["session_version"]))
        if not created.rowcount:
            raise HTTPException(status_code=401, detail="账号状态已改变，请重新登录")
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_HOURS * 3600, path="/api",
                        httponly=True, secure=request.url.scheme == "https", samesite="strict")
    response.delete_cookie("sijie_teacher_identity_session", path="/api")
    response.headers["Cache-Control"] = "no-store"


def _limit_login(request: Request, username: str) -> str:
    now = datetime.now(UTC)
    ip = request.client.host if request.client else "unknown"
    user_bucket = "user:" + _token_hash(username)
    limits = [("ip:" + _token_hash(ip), 60), (user_bucket, 10)]
    blocked = False
    with database() as connection:
        connection.execute("DELETE FROM account_login_attempts WHERE expires_at <= ?", (now.isoformat(),))
        for bucket, limit in limits:
            connection.execute("""INSERT INTO account_login_attempts VALUES (?, 1, ?)
                                  ON CONFLICT(bucket) DO UPDATE SET attempts = attempts + 1""",
                               (bucket, (now + timedelta(minutes=15)).isoformat()))
            count = connection.execute("SELECT attempts FROM account_login_attempts WHERE bucket = ?", (bucket,)).fetchone()["attempts"]
            blocked = blocked or count > limit
    if blocked:
        raise HTTPException(status_code=429, detail="登录尝试过多，请15分钟后重试", headers={"Retry-After": "900"})
    return user_bucket


def _login(payload: Credentials, request: Request, response: Response, role: str) -> dict:
    username = payload.username.strip().lower()
    bucket = _limit_login(request, username)
    account = fetch_one("SELECT * FROM user_accounts WHERE username = ?", (username,))
    valid = verify_password(payload.password, account["password_hash"] if account else _DUMMY_HASH)
    if not valid or not account or not account["enabled"] or account["role"] != role:
        raise HTTPException(status_code=401, detail="账号或密码错误，或账号已被停用")
    with database() as connection:
        connection.execute("DELETE FROM account_login_attempts WHERE bucket = ?", (bucket,))
        connection.execute("UPDATE user_accounts SET last_login_at = ? WHERE id = ?", (utc_now(), account["id"]))
    account["account_id"] = account["id"]
    _issue_session(response, request, account)
    return {"authenticated": True, "role": role, "must_change_password": bool(account["must_change_password"])}


@router.get("/status")
def status(request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    account = current_account(request)
    teacher = account if account and account["role"] == "teacher" else None
    return {"authenticated": teacher is not None, "teacher": public_account(teacher) if teacher else None,
            "role": account["role"] if account else None,
            "must_change_password": bool(account and account["must_change_password"])}


@router.get("/login-options")
def get_login_options(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return {"schools": [], "subjects": [], "password_login_required": True}


@router.post("/login")
def login(payload: Credentials, request: Request, response: Response) -> dict:
    return _login(payload, request, response, "teacher")


@admin_auth_router.get("/status")
def admin_status(request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    account = current_account(request)
    return {"authenticated": bool(account and account["role"] == "admin"),
            "account": public_account(account) if account and account["role"] == "admin" else None}


@admin_auth_router.post("/login")
def admin_login(payload: Credentials, request: Request, response: Response) -> dict:
    return _login(payload, request, response, "admin")


@router.post("/password")
def change_password(payload: PasswordChange, request: Request, response: Response) -> dict:
    account = current_account(request)
    if not account:
        raise HTTPException(status_code=401, detail="请重新登录")
    bucket = _limit_login(request, account["username"])
    existing = fetch_one("SELECT password_hash FROM user_accounts WHERE id = ?", (account["account_id"],))
    if not verify_password(payload.current_password, existing["password_hash"]):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=422, detail="新密码不能与原密码相同")
    encoded = hash_password(payload.new_password)
    with database() as connection:
        updated = connection.execute("""UPDATE user_accounts SET password_hash = ?, must_change_password = 0,
                                        session_version = session_version + 1, updated_at = ?
                                        WHERE id = ? AND session_version = ? AND enabled = 1""",
                                     (encoded, utc_now(), account["account_id"], account["session_version"]))
        if not updated.rowcount:
            raise HTTPException(status_code=401, detail="账号状态已改变，请重新登录")
        connection.execute("DELETE FROM account_sessions WHERE account_id = ?", (account["account_id"],))
        connection.execute("DELETE FROM account_login_attempts WHERE bucket = ?", (bucket,))
    account["session_version"] += 1
    _issue_session(response, request, account)
    write_audit("change_password", "user_account", account["account_id"], {})
    return {"changed": True}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        with database() as connection:
            connection.execute("DELETE FROM account_sessions WHERE token_hash = ?", (_token_hash(token),))
    response.delete_cookie(COOKIE_NAME, path="/api")
    response.delete_cookie("sijie_teacher_identity_session", path="/api")
    response.headers["Cache-Control"] = "no-store"
    return {"authenticated": False}


def provision_teacher(username: str, password: str, school_name: str, subject: str,
                      teacher_name: str, workspace_id: str | None = None, *,
                      must_change_password: bool = True) -> dict:
    username = normalize_username(username)
    encoded = hash_password(password)
    school, subject, name = school_name.strip(), subject.strip(), teacher_name.strip()
    if not all((school, subject, name)) and not workspace_id:
        raise HTTPException(status_code=422, detail="请填写学校、学科和教师姓名，或选择已有教师空间")
    now, account_id = utc_now(), "acct-" + uuid4().hex
    try:
        with database() as connection:
            if workspace_id:
                workspace = connection.execute("SELECT id FROM teacher_workspaces WHERE id = ?", (workspace_id,)).fetchone()
                if not workspace:
                    raise HTTPException(status_code=404, detail="教师空间不存在")
            else:
                key = teacher_identity_key(school, subject, name)
                workspace = connection.execute("SELECT id FROM teacher_workspaces WHERE identity_key = ?", (key,)).fetchone()
                if workspace:
                    workspace_id = workspace["id"]
                else:
                    workspace_id = "ws-" + uuid4().hex
                    connection.execute("""INSERT INTO teacher_workspaces (id, school_name, subject, teacher_name, identity_key, created_at, updated_at)
                                          VALUES (?, ?, ?, ?, ?, ?, ?)""", (workspace_id, school, subject, name, key, now, now))
            connection.execute("""INSERT INTO user_accounts (id, username, role, workspace_id, password_hash, must_change_password, created_at, updated_at)
                                  VALUES (?, ?, 'teacher', ?, ?, ?, ?, ?)""",
                               (account_id, username, workspace_id, encoded, int(must_change_password), now, now))
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="账号已存在，或该教师空间已绑定其他账号") from None
    return {"id": account_id, "username": username, "workspace_id": workspace_id}
