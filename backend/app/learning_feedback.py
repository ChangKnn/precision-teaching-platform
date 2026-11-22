"""Automatically share AI diagnoses and strong, same-task dialogue examples."""
from __future__ import annotations
import json
from fastapi import APIRouter, Depends, HTTPException
from threading import RLock
from .database import database, fetch_all, fetch_one, utc_now
from .student_api import owned_session, require_student, student_feedback_for_row

router = APIRouter(prefix='/api')
LEVELS = {'P': '前结构', 'U': '单点结构', 'M': '多点结构', 'R': '关联结构', 'EA': '抽象拓展结构'}

_sync_lock = RLock()

def eligible_rows(teaching_id: str, classroom_id: str) -> list[dict]:
    return fetch_all('''SELECT sts.*, s.name AS student_name, r.generated_json,
        r.student_feedback_text, r.report_text, r.provider, r.status AS report_status,
        r.updated_at AS report_updated_at, v.revision AS task_version_number
        FROM student_task_sessions sts JOIN students s ON s.id=sts.student_id
        JOIN student_diagnosis_reports r ON r.session_id=sts.id
        LEFT JOIN diagnosis_task_versions v ON v.id=sts.task_version_id
        WHERE sts.teaching_id=? AND s.classroom_id=? AND sts.status='submitted'
        AND r.status IN ('draft','confirmed','pushed') AND r.provider != 'mock'
        ORDER BY s.name, sts.id''', (teaching_id, classroom_id))


def diagnosis(row: dict) -> dict:
    return json.loads(row['generated_json']).get('diagnosis', {})


def current_messages(row: dict) -> list[dict]:
    # A session can retain older task versions. Only share the analyzed version.
    return fetch_all('''SELECT CASE WHEN role='student' THEN 'student' ELSE 'ai' END AS role, content FROM student_messages
        WHERE session_id=? AND task_version_id IS ? AND role IN ('student','assistant','ai') ORDER BY id''', (row['id'], row.get('task_version_id')))


def feedback_status(session: dict) -> dict:
    if session['status'] != 'submitted':
        return {'thinking_ready': False, 'examples_ready': False, 'thinking_pushed_at': None, 'examples_pushed_at': None}
    thinking = fetch_one('SELECT pushed_at FROM student_learning_feedback WHERE session_id=?', (session['id'],))
    legacy = fetch_one("SELECT updated_at AS pushed_at FROM student_diagnosis_reports WHERE session_id=? AND status='pushed'", (session['id'],))
    examples = fetch_one('''SELECT pushed_at FROM class_dialogue_examples
        WHERE teaching_id=? AND classroom_id=? AND task_version_id=?''',
        (session['teaching_id'], session['classroom_id'], session.get('task_version_id') or ''))
    return {'thinking_ready': bool(thinking or legacy), 'examples_ready': bool(examples),
            'thinking_pushed_at': (thinking or legacy or {}).get('pushed_at'),
            'examples_pushed_at': (examples or {}).get('pushed_at')}


def student_session(session_id: str, student: dict) -> dict:
    session = owned_session(session_id, student['id'])
    session['classroom_id'] = student['classroom_id']
    return session


@router.get('/student/sessions/{session_id}/learning-feedback/status')
def learning_feedback_status(session_id: str, student: dict = Depends(require_student)) -> dict:
    return feedback_status(student_session(session_id, student))


@router.get('/student/sessions/{session_id}/learning-feedback')
def learning_feedback(session_id: str, student: dict = Depends(require_student)) -> dict:
    session = student_session(session_id, student)
    if session['status'] != 'submitted':
        raise HTTPException(status_code=409, detail='提交本次任务后可查看反馈和对话示例')
    thinking = fetch_one('SELECT report_text, pushed_at FROM student_learning_feedback WHERE session_id=?', (session_id,))
    legacy = fetch_one("SELECT * FROM student_diagnosis_reports WHERE session_id=? AND status='pushed'", (session_id,))
    # A newer detailed report supersedes a previously pushed brief snapshot.
    if legacy and (not thinking or legacy['updated_at'] >= thinking['pushed_at']):
        thinking = {'report_text': student_feedback_for_row(legacy), 'pushed_at': legacy['pushed_at']}
    saved = fetch_one('''SELECT examples_json, pushed_at FROM class_dialogue_examples
        WHERE teaching_id=? AND classroom_id=? AND task_version_id=?''',
        (session['teaching_id'], student['classroom_id'], session.get('task_version_id') or ''))
    # No source IDs, names, private reports, or other students' submission files are exposed.
    examples = [{key: item[key] for key in ('title', 'level_name', 'messages')}
                for item in json.loads(saved['examples_json'])] if saved else []
    return {'thinking_feedback': thinking, 'examples': examples, 'examples_pushed_at': saved['pushed_at'] if saved else None,
            'status': feedback_status(session)}


def sync_automatic_learning(teaching_id: str, classroom_id: str) -> dict:
    """Reuse AI diagnoses without further model calls or teacher confirmation.

    Rank examples once every submission in that task version has been analyzed.
    Keep existing valid examples while additional submissions await analysis.
    """
    changed_feedback = changed_examples = 0
    with _sync_lock, database() as connection:
        connection.execute('BEGIN IMMEDIATE')
        rows = [row for row in eligible_rows(teaching_id, classroom_id) if diagnosis(row).get('level') in LEVELS]
        now = utc_now()
        for row in rows:
            text = f"### 本次思维结构\n{LEVELS[diagnosis(row)['level']]}\n\n" + student_feedback_for_row(row)
            saved = connection.execute('SELECT report_text FROM student_learning_feedback WHERE session_id=?', (row['id'],)).fetchone()
            if saved and saved['report_text'] == text:
                continue
            connection.execute('''INSERT INTO student_learning_feedback (session_id,report_text,pushed_at) VALUES (?,?,?)
                ON CONFLICT(session_id) DO UPDATE SET report_text=excluded.report_text,pushed_at=excluded.pushed_at''',
                (row['id'],text,now))
            changed_feedback += 1
        submissions = fetch_all('''SELECT sts.id, COALESCE(sts.task_version_id,'') AS version
            FROM student_task_sessions sts JOIN students s ON s.id=sts.student_id
            WHERE sts.teaching_id=? AND s.classroom_id=? AND sts.status='submitted' ''', (teaching_id,classroom_id))
        groups = {}
        for submission in submissions:
            groups.setdefault(submission['version'],set()).add(submission['id'])
        existing = {item['task_version_id']: item['examples_json'] for item in fetch_all(
            'SELECT task_version_id,examples_json FROM class_dialogue_examples WHERE teaching_id=? AND classroom_id=?',
            (teaching_id,classroom_id))}
        for version in set(groups) | set(existing):
            candidates = [row for row in rows if (row.get('task_version_id') or '') == version]
            valid_ids = {row['id'] for row in candidates}
            prior = json.loads(existing.get(version,'[]'))
            if groups.get(version) != valid_ids:
                if prior and not all(item['session_id'] in valid_ids for item in prior):
                    connection.execute('DELETE FROM class_dialogue_examples WHERE teaching_id=? AND classroom_id=? AND task_version_id=?',
                        (teaching_id,classroom_id,version))
                continue
            strong = [row for row in candidates if diagnosis(row)['level'] in ('R','EA')]
            strong.sort(key=lambda row: (-list(LEVELS).index(diagnosis(row)['level']),row['id']))
            examples = []
            for row in strong:
                messages = current_messages(row)
                if not {'student','ai'}.issubset({m['role'] for m in messages}):
                    continue
                examples.append({'session_id':row['id'],'title':f'对话示例{len(examples)+1}',
                    'level_name':LEVELS[diagnosis(row)['level']],'messages':messages})
                if len(examples) == 2:
                    break
            if len(examples) != 2:
                connection.execute('DELETE FROM class_dialogue_examples WHERE teaching_id=? AND classroom_id=? AND task_version_id=?',
                    (teaching_id,classroom_id,version))
                continue
            if prior == examples:
                continue
            connection.execute('''INSERT INTO class_dialogue_examples
                (teaching_id,classroom_id,task_version_id,examples_json,pushed_at) VALUES (?,?,?,?,?)
                ON CONFLICT(teaching_id,classroom_id,task_version_id) DO UPDATE SET
                examples_json=excluded.examples_json,pushed_at=excluded.pushed_at''',
                (teaching_id,classroom_id,version,json.dumps(examples,ensure_ascii=False),now))
            changed_examples += 1
    return {'feedback_updated':changed_feedback,'example_sets_updated':changed_examples}
