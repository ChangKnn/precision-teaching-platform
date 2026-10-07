from __future__ import annotations

import json
import hashlib
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from .config import settings


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def teacher_identity_key(school_name: str, subject: str, teacher_name: str) -> str:
    parts = [unicodedata.normalize("NFKC", value).strip().casefold() for value in (school_name, subject, teacher_name)]
    return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))


def dict_factory(cursor: sqlite3.Cursor, row: sqlite3.Row) -> dict[str, Any]:
    return {description[0]: row[index] for index, description in enumerate(cursor.description)}


def connect(database_path: Path | None = None) -> sqlite3.Connection:
    path = database_path or settings.database_path
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5, check_same_thread=False)
    connection.row_factory = dict_factory
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


@contextmanager
def database() -> Iterator[sqlite3.Connection]:
    connection = connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database() -> None:
    schema = """
    CREATE TABLE IF NOT EXISTS teacher_profiles (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        display_name TEXT NOT NULL,
        subject TEXT NOT NULL,
        years_experience INTEGER NOT NULL DEFAULT 0,
        teaching_style TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS teacher_identity_sessions (
        token_hash TEXT PRIMARY KEY,
        workspace_id TEXT,
        school_name TEXT NOT NULL,
        subject TEXT NOT NULL,
        teacher_name TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS teacher_workspaces (
        id TEXT PRIMARY KEY,
        school_name TEXT NOT NULL,
        subject TEXT NOT NULL,
        teacher_name TEXT NOT NULL,
        identity_key TEXT NOT NULL UNIQUE,
        years_experience INTEGER NOT NULL DEFAULT 0,
        teaching_style TEXT NOT NULL DEFAULT '',
        profile_completed INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS classrooms (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        student_count INTEGER NOT NULL DEFAULT 0,
        background TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS precision_teachings (
        id TEXT PRIMARY KEY,
        workspace_id TEXT REFERENCES teacher_workspaces(id),
        title TEXT NOT NULL,
        goal TEXT NOT NULL,
        content TEXT NOT NULL DEFAULT '',
        rationale TEXT NOT NULL DEFAULT '',
        subject TEXT NOT NULL,
        grade TEXT NOT NULL,
        textbook TEXT NOT NULL DEFAULT '',
        estimated_periods INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'draft',
        current_stage TEXT NOT NULL DEFAULT 'diagnosis',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS diagnosis_tasks (
        teaching_id TEXT PRIMARY KEY REFERENCES precision_teachings(id) ON DELETE CASCADE,
        diagnosis_type TEXT NOT NULL DEFAULT 'pre',
        goal TEXT NOT NULL DEFAULT '',
        task_text TEXT NOT NULL DEFAULT '',
        ai_role TEXT NOT NULL DEFAULT '',
        duration_minutes INTEGER NOT NULL DEFAULT 15,
        status TEXT NOT NULL DEFAULT 'draft',
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS diagnostic_task_recommendations (
        teaching_id TEXT PRIMARY KEY REFERENCES precision_teachings(id) ON DELETE CASCADE,
        skill_version TEXT NOT NULL,
        provider TEXT NOT NULL,
        input_hash TEXT NOT NULL,
        generated_json TEXT NOT NULL,
        generated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS diagnosis_rubrics (
        teaching_id TEXT PRIMARY KEY REFERENCES precision_teachings(id) ON DELETE CASCADE,
        skill_key TEXT NOT NULL,
        skill_version TEXT NOT NULL,
        provider TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft',
        input_hash TEXT NOT NULL,
        generated_json TEXT NOT NULL,
        current_json TEXT NOT NULL,
        generated_at TEXT NOT NULL,
        confirmed_at TEXT,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS teacher_analysis_standards (
        teaching_id TEXT PRIMARY KEY REFERENCES precision_teachings(id) ON DELETE CASCADE,
        criteria TEXT NOT NULL DEFAULT '',
        individual_enabled INTEGER NOT NULL DEFAULT 0,
        class_enabled INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS teacher_analysis_reports (
        teaching_id TEXT NOT NULL REFERENCES precision_teachings(id) ON DELETE CASCADE,
        scope TEXT NOT NULL CHECK (scope IN ('individual', 'class')),
        subject_id TEXT NOT NULL,
        skill_key TEXT NOT NULL,
        skill_version TEXT NOT NULL,
        provider TEXT NOT NULL,
        input_hash TEXT NOT NULL,
        result_json TEXT NOT NULL,
        generated_at TEXT NOT NULL,
        PRIMARY KEY (teaching_id, scope, subject_id)
    );

    CREATE TABLE IF NOT EXISTS feedback_summaries (
        teaching_id TEXT PRIMARY KEY REFERENCES precision_teachings(id) ON DELETE CASCADE,
        status TEXT NOT NULL DEFAULT 'pending',
        has_data INTEGER NOT NULL DEFAULT 0,
        total_students INTEGER NOT NULL DEFAULT 0,
        confirmed_count INTEGER NOT NULL DEFAULT 0,
        evidence_insufficient_count INTEGER NOT NULL DEFAULT 0,
        summary TEXT NOT NULL DEFAULT '',
        teacher_judgment TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS intervention_plans (
        teaching_id TEXT PRIMARY KEY REFERENCES precision_teachings(id) ON DELETE CASCADE,
        status TEXT NOT NULL DEFAULT 'draft',
        duration_minutes INTEGER NOT NULL DEFAULT 45,
        evidence_summary TEXT NOT NULL DEFAULT '',
        teacher_judgment TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS skill_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        skill_key TEXT NOT NULL,
        name TEXT NOT NULL,
        version TEXT NOT NULL,
        source_path TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        UNIQUE(skill_key, version)
    );

    CREATE TABLE IF NOT EXISTS app_state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS ai_jobs (
        id TEXT PRIMARY KEY,
        workspace_id TEXT REFERENCES teacher_workspaces(id),
        skill_key TEXT NOT NULL,
        skill_version TEXT NOT NULL,
        provider TEXT NOT NULL,
        status TEXT NOT NULL,
        input_json TEXT NOT NULL,
        output_json TEXT,
        error TEXT,
        created_at TEXT NOT NULL,
        completed_at TEXT
    );

    CREATE TABLE IF NOT EXISTS llm_request_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel TEXT NOT NULL,
        purpose TEXT NOT NULL,
        provider TEXT NOT NULL,
        endpoint TEXT NOT NULL,
        request_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        detail_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS classroom_schools (
        classroom_id TEXT PRIMARY KEY REFERENCES classrooms(id) ON DELETE CASCADE,
        school_name TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS classroom_workspaces (
        classroom_id TEXT NOT NULL REFERENCES classrooms(id) ON DELETE CASCADE,
        workspace_id TEXT NOT NULL REFERENCES teacher_workspaces(id) ON DELETE CASCADE,
        linked_at TEXT NOT NULL,
        PRIMARY KEY (classroom_id, workspace_id)
    );

    CREATE TABLE IF NOT EXISTS diagnosis_assignments (
        teaching_id TEXT NOT NULL REFERENCES precision_teachings(id) ON DELETE CASCADE,
        classroom_id TEXT NOT NULL REFERENCES classrooms(id) ON DELETE CASCADE,
        assigned_at TEXT NOT NULL,
        is_published INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (teaching_id, classroom_id)
    );

    CREATE TABLE IF NOT EXISTS students (
        id TEXT PRIMARY KEY,
        classroom_id TEXT NOT NULL REFERENCES classrooms(id),
        school_name TEXT NOT NULL,
        class_name TEXT NOT NULL,
        name TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (school_name, class_name, name)
    );

    CREATE TABLE IF NOT EXISTS student_login_sessions (
        token TEXT PRIMARY KEY,
        student_id TEXT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS student_task_sessions (
        id TEXT PRIMARY KEY,
        student_id TEXT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
        teaching_id TEXT NOT NULL REFERENCES precision_teachings(id) ON DELETE CASCADE,
        status TEXT NOT NULL DEFAULT 'in_progress',
        started_at TEXT NOT NULL,
        last_active_at TEXT NOT NULL,
        submitted_text TEXT,
        dialogue_config_json TEXT,
        submitted_at TEXT,
        UNIQUE (student_id, teaching_id)
    );

    CREATE TABLE IF NOT EXISTS student_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL REFERENCES student_task_sessions(id) ON DELETE CASCADE,
        role TEXT NOT NULL CHECK (role IN ('student', 'assistant')),
        content TEXT NOT NULL,
        provider TEXT NOT NULL DEFAULT 'local',
        external_response_id TEXT,
        execution_json TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS student_submissions (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES student_task_sessions(id) ON DELETE CASCADE,
        original_filename TEXT NOT NULL,
        stored_filename TEXT NOT NULL UNIQUE,
        content_type TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS student_diagnosis_reports (
        session_id TEXT PRIMARY KEY REFERENCES student_task_sessions(id) ON DELETE CASCADE,
        teaching_id TEXT NOT NULL REFERENCES precision_teachings(id) ON DELETE CASCADE,
        skill_key TEXT NOT NULL,
        skill_version TEXT NOT NULL,
        provider TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft',
        input_hash TEXT NOT NULL,
        generated_json TEXT NOT NULL,
        report_text TEXT NOT NULL,
        student_feedback_text TEXT NOT NULL DEFAULT '',
        generated_at TEXT NOT NULL,
        reviewed_at TEXT,
        pushed_at TEXT,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS class_diagnosis_reports (
        teaching_id TEXT NOT NULL REFERENCES precision_teachings(id) ON DELETE CASCADE,
        classroom_id TEXT NOT NULL REFERENCES classrooms(id) ON DELETE CASCADE,
        skill_key TEXT NOT NULL,
        skill_version TEXT NOT NULL,
        provider TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',
        source_hash TEXT NOT NULL,
        generated_json TEXT,
        report_text TEXT,
        error TEXT,
        generated_at TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (teaching_id, classroom_id)
    );

    CREATE TABLE IF NOT EXISTS goal_path_designs (
        teaching_id TEXT NOT NULL REFERENCES precision_teachings(id) ON DELETE CASCADE,
        classroom_id TEXT NOT NULL REFERENCES classrooms(id) ON DELETE CASCADE,
        skill_key TEXT NOT NULL,
        skill_version TEXT NOT NULL,
        provider TEXT NOT NULL,
        status TEXT NOT NULL,
        class_source_hash TEXT NOT NULL,
        input_hash TEXT NOT NULL,
        teacher_context_json TEXT NOT NULL,
        regeneration_request TEXT NOT NULL DEFAULT '',
        generated_json TEXT NOT NULL,
        report_text TEXT NOT NULL,
        generated_at TEXT NOT NULL,
        confirmed_at TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (teaching_id, classroom_id)
    );

    CREATE TABLE IF NOT EXISTS goal_path_analysis_drafts (
        teaching_id TEXT NOT NULL REFERENCES precision_teachings(id) ON DELETE CASCADE,
        classroom_id TEXT NOT NULL REFERENCES classrooms(id) ON DELETE CASCADE,
        class_source_hash TEXT NOT NULL,
        content_analysis TEXT NOT NULL,
        focus_analysis TEXT NOT NULL,
        source_note TEXT NOT NULL DEFAULT '',
        content_confirmed INTEGER NOT NULL DEFAULT 0,
        focus_confirmed INTEGER NOT NULL DEFAULT 0,
        provider TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (teaching_id, classroom_id)
    );

    CREATE TABLE IF NOT EXISTS activity_formative_designs (
        teaching_id TEXT NOT NULL REFERENCES precision_teachings(id) ON DELETE CASCADE,
        classroom_id TEXT NOT NULL REFERENCES classrooms(id) ON DELETE CASCADE,
        skill_key TEXT NOT NULL,
        skill_version TEXT NOT NULL,
        provider TEXT NOT NULL,
        status TEXT NOT NULL,
        goal_path_hash TEXT NOT NULL,
        generated_json TEXT NOT NULL,
        report_text TEXT NOT NULL,
        presentation_overrides_json TEXT NOT NULL DEFAULT '{}',
        generated_at TEXT NOT NULL,
        confirmed_at TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (teaching_id, classroom_id)
    );

    CREATE TABLE IF NOT EXISTS intervention_integration_reports (
        teaching_id TEXT NOT NULL REFERENCES precision_teachings(id) ON DELETE CASCADE,
        classroom_id TEXT NOT NULL REFERENCES classrooms(id) ON DELETE CASCADE,
        skill_key TEXT NOT NULL,
        skill_version TEXT NOT NULL,
        provider TEXT NOT NULL,
        source_hash TEXT NOT NULL,
        generated_json TEXT NOT NULL,
        generated_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (teaching_id, classroom_id)
    );

    CREATE INDEX IF NOT EXISTS idx_assignments_classroom
    ON diagnosis_assignments(classroom_id, teaching_id);

    CREATE INDEX IF NOT EXISTS idx_students_classroom
    ON students(classroom_id);

    CREATE INDEX IF NOT EXISTS idx_student_task_sessions_teaching_status
    ON student_task_sessions(teaching_id, status);

    CREATE INDEX IF NOT EXISTS idx_student_messages_session_created
    ON student_messages(session_id, created_at);

    CREATE INDEX IF NOT EXISTS idx_student_submissions_session
    ON student_submissions(session_id);
    """
    now = utc_now()
    with database() as connection:
        connection.executescript(schema)
        teaching_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(precision_teachings)").fetchall()
        }
        if "content" not in teaching_columns:
            connection.execute("ALTER TABLE precision_teachings ADD COLUMN content TEXT NOT NULL DEFAULT ''")
        if "workspace_id" not in teaching_columns:
            connection.execute("ALTER TABLE precision_teachings ADD COLUMN workspace_id TEXT REFERENCES teacher_workspaces(id)")
        workspace_columns = {row["name"] for row in connection.execute("PRAGMA table_info(teacher_workspaces)").fetchall()}
        if "profile_completed" not in workspace_columns:
            connection.execute("ALTER TABLE teacher_workspaces ADD COLUMN profile_completed INTEGER NOT NULL DEFAULT 0")
            connection.execute("""UPDATE teacher_workspaces SET profile_completed = 1
                WHERE teaching_style != '' OR EXISTS (
                    SELECT 1 FROM precision_teachings p WHERE p.workspace_id = teacher_workspaces.id
                )""")
        assignment_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(diagnosis_assignments)").fetchall()
        }
        if "is_published" not in assignment_columns:
            connection.execute("ALTER TABLE diagnosis_assignments ADD COLUMN is_published INTEGER NOT NULL DEFAULT 1")
        identity_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(teacher_identity_sessions)").fetchall()
        }
        if "workspace_id" not in identity_columns:
            connection.execute("ALTER TABLE teacher_identity_sessions ADD COLUMN workspace_id TEXT")
        job_columns = {row["name"] for row in connection.execute("PRAGMA table_info(ai_jobs)").fetchall()}
        if "workspace_id" not in job_columns:
            connection.execute("ALTER TABLE ai_jobs ADD COLUMN workspace_id TEXT REFERENCES teacher_workspaces(id)")
        session_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(student_task_sessions)").fetchall()
        }
        if "submitted_text" not in session_columns:
            connection.execute("ALTER TABLE student_task_sessions ADD COLUMN submitted_text TEXT")
        if "dialogue_config_json" not in session_columns:
            connection.execute("ALTER TABLE student_task_sessions ADD COLUMN dialogue_config_json TEXT")
        message_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(student_messages)").fetchall()
        }
        if "execution_json" not in message_columns:
            connection.execute("ALTER TABLE student_messages ADD COLUMN execution_json TEXT")
        report_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(student_diagnosis_reports)").fetchall()
        }
        if "student_feedback_text" not in report_columns:
            connection.execute("ALTER TABLE student_diagnosis_reports ADD COLUMN student_feedback_text TEXT NOT NULL DEFAULT ''")
        activity_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(activity_formative_designs)").fetchall()
        }
        if "presentation_overrides_json" not in activity_columns:
            connection.execute("ALTER TABLE activity_formative_designs ADD COLUMN presentation_overrides_json TEXT NOT NULL DEFAULT '{}'")
        goal_path_columns = {row["name"] for row in connection.execute("PRAGMA table_info(goal_path_designs)").fetchall()}
        if "regeneration_request" not in goal_path_columns:
            connection.execute("ALTER TABLE goal_path_designs ADD COLUMN regeneration_request TEXT NOT NULL DEFAULT ''")
        if settings.seed_demo_data:
            connection.execute(
                """
                INSERT OR IGNORE INTO teacher_profiles
                (id, display_name, subject, years_experience, teaching_style, updated_at)
                VALUES (1, ?, ?, ?, ?, ?)
                """,
                (
                    "林老师",
                    "数学",
                    12,
                    "重视学生表达与同伴讨论，习惯从典型错误切入，通过追问帮助学生建立概念和关系；课堂节奏清晰，倾向使用纸质学习单。",
                    now,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO app_state (key, value, updated_at)
                VALUES ('current_teaching_id', 'pt-basic-inequality', ?)
                """,
                (now,),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO precision_teachings
                (id, title, goal, content, rationale, subject, grade, textbook,
                 estimated_periods, status, current_stage, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "pt-function-monotonicity",
                    "函数单调性中的推理表达",
                    "已填写主题，精准教学目标尚未确认。",
                    "函数单调性的概念、判定依据与推理表达。",
                    "",
                    "数学",
                    "高一",
                    "",
                    2,
                    "draft",
                    "diagnosis",
                    now,
                    now,
                ),
            )
            classrooms = [
                (
                    "class-1-3",
                    "高一（3）班",
                    40,
                    "学生愿意口头表达，但书面论证完整性差异较大；教室可使用投影，每组有 1 台平板。",
                    now,
                    now,
                ),
                (
                    "class-1-5",
                    "高一（5）班",
                    42,
                    "学生独立作答速度差异较大，小组合作规范较稳定。",
                    now,
                    now,
                ),
            ]
            connection.executemany(
                """
                INSERT OR IGNORE INTO classrooms
                (id, name, student_count, background, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                classrooms,
            )
            connection.executemany(
                """
                INSERT OR IGNORE INTO classroom_schools (classroom_id, school_name, updated_at)
                VALUES (?, ?, ?)
                """,
                [
                    ("class-1-3", "杭州市求知中学", now),
                    ("class-1-5", "杭州市求知中学", now),
                ],
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO precision_teachings
                (id, title, goal, content, rationale, subject, grade, textbook,
                 estimated_periods, status, current_stage, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "pt-basic-inequality",
                    "基本不等式中的模型与论证",
                    "精准识别并提升学生从提取数量关系、建立约束模型到完整论证最值结论的思维结构。",
                    "基本不等式实际应用中的数量关系提取、约束模型、取等条件与最值论证。",
                    "聚焦数学建模与逻辑推理中的关键障碍。",
                    "数学",
                    "高一",
                    "人教 A 版 必修一",
                    3,
                    "active",
                    "feedback",
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE precision_teachings
                SET content = ?
                WHERE id = 'pt-basic-inequality' AND TRIM(content) = ''
                """,
                ("基本不等式实际应用中的数量关系提取、约束模型、取等条件与最值论证。",),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO diagnosis_tasks
                (teaching_id, diagnosis_type, goal, task_text, ai_role, duration_minutes, status, updated_at)
                VALUES (?, 'pre', ?, ?, ?, 15, 'published', ?)
                """,
                (
                    "pt-basic-inequality",
                    "了解学生能否从围栏总长中提取约束关系，建立面积模型，并说明使用基本不等式得到最大值及取等条件的理由。",
                    "学校准备使用20米围栏围成长方形劳动实践区。请确定怎样设计长和宽能够使面积最大，并提交包含模型、推理、取等条件和结论的完整论证。",
                    "中性引导者：只追问理由与关系，不提供公式、步骤、正误判断或答案方向。",
                    now,
                ),
            )
            connection.execute("PRAGMA optimize")
            connection.execute(
                """
                INSERT OR IGNORE INTO feedback_summaries
                (teaching_id, status, has_data, total_students, confirmed_count,
                 evidence_insufficient_count, summary, teacher_judgment, updated_at)
                VALUES (?, 'pending', 1, 40, 34, 3, ?, ?, ?)
                """,
                (
                    "pt-basic-inequality",
                    "本班40名学生中，17名学生已能整合约束、基本不等式、取等条件与最大值结论；12名学生能够列出相关要素但关系不完整；3名学生证据不足。",
                    "学生已经学习基本不等式的形式，但容易把试算结果当作最大值证明，需要重点建立约束、方法、取等条件和结论之间的关系。",
                    now,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO intervention_plans
                (teaching_id, status, duration_minutes, evidence_summary, teacher_judgment, updated_at)
                VALUES (?, 'draft', 45, ?, ?, ?)
                """,
                (
                    "pt-basic-inequality",
                    "本班40名学生中，17名学生已能整合关键关系，12名学生论证关系不完整，3名学生证据不足。",
                    "课堂中需要比较典型作答，重点建立约束条件、基本不等式、取等条件和结论之间的关系。",
                    now,
                ),
            )
        # Preserve existing teaching data under the matching legacy teacher/subject
        # spaces. Once assigned, a teaching is never silently moved on startup.
        legacy = connection.execute("SELECT * FROM teacher_profiles WHERE id = 1").fetchone()
        school = connection.execute("SELECT school_name FROM classroom_schools ORDER BY school_name LIMIT 1").fetchone()
        if legacy and school:
            subjects = connection.execute(
                "SELECT DISTINCT subject FROM precision_teachings WHERE workspace_id IS NULL"
            ).fetchall()
            for row in subjects:
                subject = row["subject"]
                key = teacher_identity_key(school["school_name"], subject, legacy["display_name"])
                workspace_id = "ws-legacy-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
                connection.execute(
                    """INSERT OR IGNORE INTO teacher_workspaces
                       (id, school_name, subject, teacher_name, identity_key, years_experience,
                        teaching_style, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (workspace_id, school["school_name"], subject, legacy["display_name"], key,
                     legacy["years_experience"], legacy["teaching_style"], now, now),
                )
                owner = connection.execute("SELECT id FROM teacher_workspaces WHERE identity_key = ?", (key,)).fetchone()
                connection.execute(
                    "UPDATE precision_teachings SET workspace_id = ? WHERE workspace_id IS NULL AND subject = ?",
                    (owner["id"], subject),
                )
                connection.execute("UPDATE teacher_workspaces SET profile_completed = 1 WHERE id = ?", (owner["id"],))
        connection.execute(
            """INSERT OR IGNORE INTO diagnosis_assignments (teaching_id, classroom_id, assigned_at)
               SELECT d.teaching_id, cs.classroom_id, ? FROM diagnosis_tasks d
               JOIN precision_teachings p ON p.id = d.teaching_id
               JOIN teacher_workspaces w ON w.id = p.workspace_id
               JOIN classroom_schools cs ON cs.school_name = w.school_name
               JOIN classrooms c ON c.id = cs.classroom_id
               WHERE d.status = 'published' AND w.id LIKE 'ws-legacy-%'
                 AND c.created_at <= w.created_at""",
            (now,),
        )
        if not connection.execute(
            "SELECT 1 FROM app_state WHERE key = 'classroom_workspace_migration_v1'"
        ).fetchone():
            # Existing seeded classrooms belong to the original teacher spaces.
            # Preserve classes created later by another teacher when their own
            # published teaching was assigned to that class. Do not transfer
            # school-wide assignments from the old behavior as ownership.
            connection.execute(
                """INSERT OR IGNORE INTO classroom_workspaces (classroom_id, workspace_id, linked_at)
                   SELECT cs.classroom_id, w.id, ? FROM classroom_schools cs
                   JOIN teacher_workspaces w ON w.school_name = cs.school_name
                   JOIN classrooms c ON c.id = cs.classroom_id
                   WHERE w.id LIKE 'ws-legacy-%' AND c.created_at <= w.created_at""",
                (now,),
            )
            connection.execute(
                """INSERT OR IGNORE INTO classroom_workspaces (classroom_id, workspace_id, linked_at)
                   SELECT DISTINCT da.classroom_id, p.workspace_id, ? FROM diagnosis_assignments da
                   JOIN precision_teachings p ON p.id = da.teaching_id
                   JOIN teacher_workspaces w ON w.id = p.workspace_id
                   JOIN classrooms c ON c.id = da.classroom_id
                   WHERE p.workspace_id IS NOT NULL AND w.id NOT LIKE 'ws-legacy-%'
                     AND c.created_at >= w.created_at""",
                (now,),
            )
            connection.execute(
                "INSERT INTO app_state (key, value, updated_at) VALUES ('classroom_workspace_migration_v1', 'done', ?)",
                (now,),
            )
        if not connection.execute(
            "SELECT 1 FROM app_state WHERE key = 'diagnosis_publication_migration_v1'"
        ).fetchone():
            # Earlier releases displayed a checked class without persisting the
            # selection. Keep classes with student work or a generated report;
            # retain the original demo's checked class as its initial choice.
            connection.execute(
                """UPDATE diagnosis_assignments SET is_published = CASE WHEN
                     classroom_id = 'class-1-3' AND teaching_id = 'pt-basic-inequality'
                     OR EXISTS (
                       SELECT 1 FROM student_task_sessions s JOIN students st ON st.id = s.student_id
                       WHERE s.teaching_id = diagnosis_assignments.teaching_id
                         AND st.classroom_id = diagnosis_assignments.classroom_id
                     )
                     OR EXISTS (
                       SELECT 1 FROM class_diagnosis_reports r
                       WHERE r.teaching_id = diagnosis_assignments.teaching_id
                         AND r.classroom_id = diagnosis_assignments.classroom_id
                     ) THEN 1 ELSE 0 END"""
            )
            connection.execute(
                "INSERT INTO app_state (key, value, updated_at) VALUES ('diagnosis_publication_migration_v1', 'done', ?)",
                (now,),
            )


def fetch_one(query: str, parameters: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    with database() as connection:
        return connection.execute(query, parameters).fetchone()


def fetch_all(query: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with database() as connection:
        return connection.execute(query, parameters).fetchall()


def write_audit(action: str, entity_type: str, entity_id: str, detail: dict[str, Any]) -> None:
    with database() as connection:
        connection.execute(
            """
            INSERT INTO audit_logs (action, entity_type, entity_id, detail_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (action, entity_type, entity_id, json.dumps(detail, ensure_ascii=False), utc_now()),
        )
