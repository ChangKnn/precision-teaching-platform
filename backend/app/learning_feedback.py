"""Teacher-reviewed feedback and dialogue examples, persisted for submitted students."""
from __future__ import annotations
import json
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from .database import database, fetch_all, fetch_one, utc_now, write_audit
from .student_api import owned_session, require_student, student_feedback_for_row

router = APIRouter(prefix='/api')
LEVELS = {'P': '前结构', 'U': '单点结构', 'M': '多点结构', 'R': '关联结构', 'EA': '抽象拓展结构'}


class ExampleSelection(BaseModel):
    session_ids: list[str] = Field(min_length=2, max_length=2)


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


@router.get('/precision-teachings/{teaching_id}/classrooms/{classroom_id}/learning-sharing')
def sharing_state(teaching_id: str, classroom_id: str) -> dict:
    rows = eligible_rows(teaching_id, classroom_id)
    rows.sort(key=lambda row: -list(LEVELS).index(diagnosis(row).get('level')) if diagnosis(row).get('level') in LEVELS else 1)
    candidates = []
    for row in rows:
        level = diagnosis(row).get('level')
        if level not in LEVELS:
            continue
        candidates.append({'session_id': row['id'], 'student_name': row['student_name'],
                           'task_version_id': row.get('task_version_id') or '', 'task_version_number': row.get('task_version_number'), 'level': level, 'level_name': LEVELS[level]})
    publications = fetch_all('''SELECT task_version_id, examples_json, pushed_at FROM class_dialogue_examples
        WHERE teaching_id=? AND classroom_id=?''', (teaching_id, classroom_id))
    return {'candidates': candidates, 'publications': [
        {'task_version_id': row['task_version_id'], 'session_ids': [item['session_id'] for item in json.loads(row['examples_json'])],
         'pushed_at': row['pushed_at']} for row in publications]}


@router.post('/precision-teachings/{teaching_id}/classrooms/{classroom_id}/learning-sharing/thinking-feedback')
def push_thinking_feedback(teaching_id: str, classroom_id: str) -> dict:
    rows = [row for row in eligible_rows(teaching_id, classroom_id) if diagnosis(row).get('level') in LEVELS]
    if not rows:
        raise HTTPException(status_code=409, detail='请先完成学生的思维诊断')
    now = utc_now()
    with database() as connection:
        for row in rows:
            text = f"### 本次思维结构\n{LEVELS[diagnosis(row)['level']]}\n\n" + student_feedback_for_row(row)
            connection.execute('''INSERT INTO student_learning_feedback (session_id, report_text, pushed_at)
                VALUES (?,?,?) ON CONFLICT(session_id) DO UPDATE SET report_text=excluded.report_text, pushed_at=excluded.pushed_at''',
                (row['id'], text, now))
    write_audit('push_thinking_feedback', 'precision_teaching', teaching_id, {'classroom_id': classroom_id, 'count': len(rows)})
    return {'pushed_count': len(rows), 'pushed_at': now}


@router.get('/precision-teachings/{teaching_id}/classrooms/{classroom_id}/learning-sharing/dialogues/{session_id}')
def preview_dialogue(teaching_id: str, classroom_id: str, session_id: str) -> dict:
    row = next((item for item in eligible_rows(teaching_id, classroom_id) if item['id'] == session_id), None)
    if not row:
        raise HTTPException(status_code=404, detail='没有可推荐的已分析对话')
    return {'messages': current_messages(row)}


@router.post('/precision-teachings/{teaching_id}/classrooms/{classroom_id}/learning-sharing/examples')
def push_examples(teaching_id: str, classroom_id: str, payload: ExampleSelection) -> dict:
    if len(set(payload.session_ids)) != 2:
        raise HTTPException(status_code=422, detail='请选择两名不同学生的对话')
    by_id = {row['id']: row for row in eligible_rows(teaching_id, classroom_id)}
    rows = [by_id.get(id) for id in payload.session_ids]
    if any(row is None or diagnosis(row).get('level') not in LEVELS for row in rows):
        raise HTTPException(status_code=422, detail='请从本班已完成思维诊断的学生中选择')
    versions = {row.get('task_version_id') or '' for row in rows}
    if len(versions) != 1:
        raise HTTPException(status_code=422, detail='请选择同一任务版本的两份对话')
    examples = []
    for index, row in enumerate(rows):
        messages = current_messages(row)
        if not {'student', 'ai'}.issubset({m['role'] for m in messages}):
            raise HTTPException(status_code=422, detail='所选记录缺少完整的学生与 AI 对话')
        examples.append({'session_id': row['id'], 'title': f'对话示例{index+1}',
                         'level_name': LEVELS[diagnosis(row)['level']], 'messages': messages})
    now = utc_now()
    with database() as connection:
        connection.execute('''INSERT INTO class_dialogue_examples
            (teaching_id,classroom_id,task_version_id,examples_json,pushed_at) VALUES (?,?,?,?,?)
            ON CONFLICT(teaching_id,classroom_id,task_version_id) DO UPDATE SET
            examples_json=excluded.examples_json,pushed_at=excluded.pushed_at''',
            (teaching_id, classroom_id, next(iter(versions)), json.dumps(examples, ensure_ascii=False), now))
    write_audit('push_dialogue_examples', 'precision_teaching', teaching_id, {'classroom_id': classroom_id, 'count': 2})
    return sharing_state(teaching_id, classroom_id)


@router.delete('/precision-teachings/{teaching_id}/classrooms/{classroom_id}/learning-sharing/examples')
def withdraw_examples(teaching_id: str, classroom_id: str) -> dict:
    with database() as connection:
        connection.execute('DELETE FROM class_dialogue_examples WHERE teaching_id=? AND classroom_id=?', (teaching_id, classroom_id))
    return sharing_state(teaching_id, classroom_id)
