"""Immutable published tasks; student evidence keeps the version actually seen."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from uuid import uuid4

from ..database import database, fetch_all, fetch_one, utc_now
from .task_attachment_data import append_attachment_text,public_attachments


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def get_version(version_id: str | None) -> dict | None:
    row = fetch_one('SELECT * FROM diagnosis_task_versions WHERE id = ?', (version_id,)) if version_id else None
    if row:
        row['snapshot'] = json.loads(row['snapshot_json'])
    return row


def _snapshot(teaching_id: str, connection: sqlite3.Connection) -> dict:
    task = connection.execute('SELECT * FROM diagnosis_tasks WHERE teaching_id = ?', (teaching_id,)).fetchone()
    teaching = connection.execute('''SELECT p.*, COALESCE(tw.teacher_name,'教师') AS teacher_name FROM precision_teachings p
        LEFT JOIN teacher_workspaces tw ON tw.id=p.workspace_id WHERE p.id=?''', (teaching_id,)).fetchone()
    images = connection.execute('SELECT * FROM diagnosis_task_images WHERE teaching_id=? ORDER BY created_at,id', (teaching_id,)).fetchall()
    notes = connection.execute('SELECT geometry_notes FROM diagnosis_task_materials WHERE teaching_id=?', (teaching_id,)).fetchone()
    reading = connection.execute('SELECT * FROM diagnosis_image_readings WHERE teaching_id=?', (teaching_id,)).fetchone()
    text = task['task_text']
    if notes and notes['geometry_notes'].strip():
        text += '\n\n【题图条件与补充说明（教师已核对）】\n' + notes['geometry_notes'].strip()
    image_hash = hashlib.sha256(json.dumps([{key: image[key] for key in ('id','sha256')} for image in images], sort_keys=True).encode()).hexdigest()
    if images and reading and reading['image_hash'] == image_hash and reading['status'] == 'ready':
        text += '\n\n【图片附件内容（任务材料，非学生作答）】\n' + reading['task_text']
        if reading['diagram_notes'].strip():
            text += '\n【图形、图表与材料标注】\n' + reading['diagram_notes']
    rubric = connection.execute('SELECT * FROM diagnosis_rubrics WHERE teaching_id=?', (teaching_id,)).fetchone()
    attachments=connection.execute('SELECT * FROM diagnosis_task_attachments WHERE teaching_id=? ORDER BY created_at,id',(teaching_id,)).fetchall()
    snapshot = {
        'teaching': {key: teaching[key] for key in ('id','title','subject','grade','textbook','goal','content','education_stage','course_name','major','teacher_name')},
        'task': {key: task[key] for key in ('goal','task_text','ai_role','duration_minutes','diagnosis_type','design_intent')},
        'task_text': append_attachment_text(text,attachments), 'images': images,
        'role_knowledge': connection.execute('SELECT id,original_filename,sha256,content_text FROM diagnosis_role_knowledge WHERE teaching_id=? ORDER BY created_at,id',(teaching_id,)).fetchall(),
        'rubric': json.loads(rubric['current_json']) if rubric else None,
        'rubric_confirmed': bool(rubric and rubric['status'] == 'confirmed'),
    }
    if attachments:snapshot.update(attachments=attachments,student_task_text=text)
    return snapshot


def preserve_published_task(teaching_id: str, connection: sqlite3.Connection, *, publish: bool = False) -> str | None:
    """Call before any edit, within the editor's write transaction; never rewrite evidence."""
    task = connection.execute('SELECT * FROM diagnosis_tasks WHERE teaching_id=?', (teaching_id,)).fetchone()
    if not task:
        return None
    current_id = task['published_version_id']
    has_sessions = connection.execute('SELECT 1 FROM student_task_sessions WHERE teaching_id=? LIMIT 1', (teaching_id,)).fetchone()
    if not current_id or publish:
        if not publish and task['status'] != 'published' and not has_sessions:
            return None
        snapshot = _snapshot(teaching_id, connection)
        source_hash = _hash(snapshot)
        current = connection.execute('SELECT * FROM diagnosis_task_versions WHERE id=?', (current_id,)).fetchone()
        if not current or current['source_hash'] != source_hash:
            revision = connection.execute('SELECT COALESCE(MAX(revision),0)+1 AS n FROM diagnosis_task_versions WHERE teaching_id=?', (teaching_id,)).fetchone()['n']
            current_id = 'taskv-' + uuid4().hex
            evidence_data={key:snapshot[key] for key in ('teaching','task_text','images')}
            if snapshot.get('attachments'):evidence_data['attachments']=snapshot['attachments']
            evidence_hash = _hash(evidence_data)
            connection.execute('''INSERT INTO diagnosis_task_versions
                (id,teaching_id,revision,source_hash,evidence_hash,snapshot_json,created_at) VALUES (?,?,?,?,?,?,?)''',
                (current_id,teaching_id,revision,source_hash,evidence_hash,_json(snapshot),utc_now()))
            connection.execute('UPDATE diagnosis_tasks SET published_version_id=? WHERE teaching_id=?', (current_id,teaching_id))
    # Legacy sessions/messages predate task versions. Attach them before editing.
    if current_id:
        connection.execute('UPDATE student_task_sessions SET task_version_id=? WHERE teaching_id=? AND task_version_id IS NULL', (current_id,teaching_id))
        connection.execute('''UPDATE student_messages SET task_version_id=(
                SELECT s.task_version_id FROM student_task_sessions s WHERE s.id=student_messages.session_id)
            WHERE task_version_id IS NULL AND session_id IN (
                SELECT id FROM student_task_sessions WHERE teaching_id=?)''', (teaching_id,))
    return current_id


def published_version(teaching_id: str) -> dict | None:
    task = fetch_one('SELECT published_version_id FROM diagnosis_tasks WHERE teaching_id=?', (teaching_id,))
    current = get_version(task['published_version_id']) if task else None
    if current:
        return current
    with database() as connection:
        connection.execute('BEGIN IMMEDIATE')
        version_id = preserve_published_task(teaching_id, connection)
    return get_version(version_id)


def preserve_legacy_task_versions() -> None:
    with database() as connection:
        connection.execute('BEGIN IMMEDIATE')
        tasks = connection.execute('''SELECT d.teaching_id FROM diagnosis_tasks d
            WHERE d.published_version_id IS NULL AND (d.status='published' OR EXISTS (
                SELECT 1 FROM student_task_sessions s WHERE s.teaching_id=d.teaching_id))''').fetchall()
        for task in tasks:
            preserve_published_task(task['teaching_id'], connection)


def session_version(session: dict, *, refresh: bool = True) -> dict | None:
    """Once started, a session always keeps its assigned version (including active work)."""
    if session.get('task_version_id'):
        return get_version(session['task_version_id'])
    latest = published_version(session['teaching_id'])
    if not latest:
        return None
    with database() as connection:
        connection.execute('UPDATE student_task_sessions SET task_version_id=? WHERE id=? AND task_version_id IS NULL',
                           (latest['id'], session['id']))
        row = connection.execute('SELECT task_version_id FROM student_task_sessions WHERE id=?', (session['id'],)).fetchone()
    return get_version(row['task_version_id']) if row else latest


def version_task(version: dict) -> dict:
    snapshot = version['snapshot']
    teaching = snapshot['teaching']
    return {
        'teaching_id': version['teaching_id'], 'title': teaching['title'], 'subject': teaching['subject'],
        'course_name':teaching.get('course_name',''),'education_stage':teaching.get('education_stage',''),
        'teacher_name': teaching['teacher_name'], 'task_text': snapshot.get('student_task_text',snapshot['task_text']),
        'duration_minutes': snapshot['task']['duration_minutes'],
        'task_version_id': version['id'], 'task_version_number': version['revision'],
        'task_images': [{'id': image['id'], 'file_url': f"/api/student/tasks/{version['teaching_id']}/versions/{version['id']}/images/{image['id']}"} for image in snapshot['images']],
        'task_attachments':public_attachments(snapshot.get('attachments',[]),f"/api/student/tasks/{version['teaching_id']}/versions/{version['id']}/attachments"),
    }


def version_config(version: dict) -> dict:
    from .education_context import higher_education_fields
    from .student_dialogue_skill import _teacher_contract
    snapshot = version['snapshot']
    teaching, task = snapshot['teaching'], snapshot['task']
    diagnostic_task = {'task_id':version['teaching_id'], 'name':teaching['title'], 'task_content':snapshot['task_text']}
    if 10 <= int(task['duration_minutes']) <= 30:
        diagnostic_task['estimated_duration_minutes'] = int(task['duration_minutes'])
    contract = _teacher_contract(task['ai_role'], 'published', version['id'])
    documents = snapshot.get('role_knowledge',[])
    if documents:
        contract['role_knowledge'] = [{'source_id':item['id'],'content':item['content_text']} for item in documents]
    return {
        'precision_teaching_context': {
            **higher_education_fields(teaching), 'subject': teaching['subject'] or '未提供',
            'grade': teaching['grade'] or '未提供', 'textbook_version_and_chapter':teaching['textbook'] or '未提供',
            'precision_teaching_topic': teaching['title'], 'precision_teaching_goals':[teaching['goal'] or '未提供'],
            'precision_teaching_content':[line.strip() for line in teaching['content'].splitlines() if line.strip()] or ['未提供'],
        },
        'diagnostic_task':diagnostic_task,
        'ai_dialogue_contract': contract,
    }


def version_history(session_id: str, version: dict | None) -> list[dict]:
    """Use only messages recorded for the session's fixed publication."""
    if not version:
        return fetch_all('SELECT id,role,content FROM student_messages WHERE session_id=? ORDER BY id', (session_id,))
    return fetch_all('''SELECT m.id,m.role,m.content FROM student_messages m
        JOIN diagnosis_task_versions v ON v.id=m.task_version_id
        WHERE m.session_id=? AND v.id=? ORDER BY m.id''', (session_id,version['id']))
