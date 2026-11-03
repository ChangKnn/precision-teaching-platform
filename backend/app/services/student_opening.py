"""One teacher-role opening per immutable published task version."""
from __future__ import annotations

from copy import deepcopy
import json
import logging
from threading import Lock

from ..database import database, fetch_one, utc_now
from . import student_dialogue_skill as dialogue
from .task_versions import get_version, version_config

_locks_guard = Lock()
_locks: dict[str, Lock] = {}


def shared_opening(version: dict, session_id: str, student_id: str) -> tuple[str, dict]:
    version_id = version['id']
    with _locks_guard:
        lock = _locks.setdefault(version_id, Lock())
    with lock:
        cached = fetch_one('SELECT * FROM student_task_openings WHERE task_version_id=?', (version_id,))
        if cached:
            return cached['content'], json.loads(cached['execution_json'])
        config = version_config(version)
        # Existing openings are server-validated, first assistant turns only.
        # Never reuse a student's message or an AI reply to a student's answer.
        first = fetch_one('''SELECT m.content,m.execution_json FROM student_messages m
            JOIN student_task_sessions s ON s.id=m.session_id
            WHERE s.task_version_id=? AND m.task_version_id=? AND m.role='assistant'
              AND NOT EXISTS (SELECT 1 FROM student_messages earlier
                  WHERE earlier.session_id=m.session_id AND earlier.id<m.id)
            ORDER BY m.id LIMIT 1''', (version_id, version_id))
        execution = json.loads(first['execution_json'] or '{}') if first else {}
        contract = config['ai_dialogue_contract']
        if (first and first['content'].strip() and execution.get('contract_version') == contract['contract_version']
                and execution.get('role_applied') == contract['ai_role']):
            content = first['content']
        else:
            content, execution = dialogue.initial_skill_message(config, session_id, student_id)
        with database() as connection:
            connection.execute('''INSERT OR IGNORE INTO student_task_openings
                (task_version_id,content,execution_json,created_at) VALUES (?,?,?,?)''',
                (version_id, content, json.dumps(execution, ensure_ascii=False), utc_now()))
        cached = fetch_one('SELECT * FROM student_task_openings WHERE task_version_id=?', (version_id,))
        return cached['content'], deepcopy(json.loads(cached['execution_json']))


def prepare_opening(version_id: str) -> None:
    """Warm after publishing without delaying the teacher's response."""
    try:
        version = get_version(version_id)
        if version:
            shared_opening(version, 'opening-preview', 'opening-preview')
    except Exception:
        # Keep the published task usable; a student opening can retry preparation.
        logging.getLogger(__name__).exception('Task opening preparation failed for version %s', version_id)
