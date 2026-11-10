"""Update teacher identity without moving shared classrooms or student records."""
import sqlite3

from fastapi import HTTPException

from ..database import database, teacher_identity_key, utc_now


def update_identity(workspace_id: str, school_name: str | None, subject: str, teacher_name: str, *, years_experience=None, teaching_style=None) -> dict:
    subject, teacher_name = subject.strip(), teacher_name.strip()
    school_name = school_name.strip() if school_name is not None else None
    if not subject or not teacher_name or school_name == '':
        raise HTTPException(status_code=422, detail='学校、学科和教师姓名不能为空')
    try:
        with database() as connection:
            connection.execute('BEGIN IMMEDIATE')
            current = connection.execute('SELECT * FROM teacher_workspaces WHERE id=?', (workspace_id,)).fetchone()
            if not current:
                raise HTTPException(status_code=404, detail='教师信息不存在')
            school = school_name if school_name is not None else current['school_name']
            key = teacher_identity_key(school, subject, teacher_name)
            duplicate = connection.execute('SELECT id FROM teacher_workspaces WHERE identity_key=? AND id<>?', (key, workspace_id)).fetchone()
            if duplicate:
                raise HTTPException(status_code=409, detail='该学校、学科、教师姓名对应的空间已存在')
            connection.execute('''UPDATE teacher_workspaces SET school_name=?, subject=?, teacher_name=?, identity_key=?,
                years_experience=?, teaching_style=?, profile_completed=?, updated_at=? WHERE id=?''',
                (school, subject, teacher_name, key,
                 current['years_experience'] if years_experience is None else years_experience,
                 current['teaching_style'] if teaching_style is None else teaching_style,
                 current['profile_completed'] if years_experience is None else 1, utc_now(), workspace_id))
            return {'school_name': school, 'subject': subject, 'teacher_name': teacher_name}
    except sqlite3.IntegrityError as error:
        raise HTTPException(status_code=409, detail='该学校、学科、教师姓名对应的空间已存在') from error
