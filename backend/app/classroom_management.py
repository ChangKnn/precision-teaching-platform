"""Reversible removal of a teacher's class link; retain student evidence."""
import json

from fastapi import APIRouter, HTTPException, Request
from .database import database, fetch_all, utc_now, write_audit

router=APIRouter(prefix='/api/classrooms')


def deleted_classrooms(workspace_id: str) -> list[dict]:
    return fetch_all('''SELECT c.*,a.removed_at FROM classroom_archives a
        JOIN classrooms c ON c.id=a.classroom_id WHERE a.workspace_id=? ORDER BY a.removed_at DESC''',(workspace_id,))


@router.delete('/{classroom_id}')
def delete_classroom(classroom_id: str,request: Request) -> dict:
    workspace_id=request.state.teacher_workspace_id
    with database() as connection:
        connection.execute('BEGIN IMMEDIATE')
        owned=connection.execute('SELECT 1 FROM classroom_workspaces WHERE classroom_id=? AND workspace_id=?',(classroom_id,workspace_id)).fetchone()
        if not owned:
            if connection.execute('SELECT 1 FROM classroom_archives WHERE classroom_id=? AND workspace_id=?',(classroom_id,workspace_id)).fetchone():
                return {'deleted':True,'id':classroom_id}
            raise HTTPException(status_code=404,detail='班级不存在')
        assignments=connection.execute('''SELECT teaching_id,is_published FROM diagnosis_assignments
            WHERE classroom_id=? AND teaching_id IN (SELECT id FROM precision_teachings WHERE workspace_id=?)''',(classroom_id,workspace_id)).fetchall()
        connection.execute('INSERT INTO classroom_archives VALUES (?,?,?,?)',(classroom_id,workspace_id,json.dumps(assignments),utc_now()))
        connection.execute('''UPDATE diagnosis_assignments SET is_published=0 WHERE classroom_id=?
            AND teaching_id IN (SELECT id FROM precision_teachings WHERE workspace_id=?)''',(classroom_id,workspace_id))
        connection.execute('DELETE FROM classroom_workspaces WHERE classroom_id=? AND workspace_id=?',(classroom_id,workspace_id))
    write_audit('archive','classroom',classroom_id,{'workspace_id':workspace_id})
    return {'deleted':True,'id':classroom_id}


@router.post('/{classroom_id}/restore')
def restore_classroom(classroom_id: str,request: Request) -> dict:
    workspace_id=request.state.teacher_workspace_id
    with database() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row=connection.execute('SELECT * FROM classroom_archives WHERE classroom_id=? AND workspace_id=?',(classroom_id,workspace_id)).fetchone()
        if not row:raise HTTPException(status_code=404,detail='已删除的班级不存在')
        connection.execute('INSERT OR IGNORE INTO classroom_workspaces VALUES (?,?,?)',(classroom_id,workspace_id,utc_now()))
        for assignment in json.loads(row['assignments_json']):
            connection.execute('''UPDATE diagnosis_assignments SET is_published=? WHERE classroom_id=? AND teaching_id=?
                AND teaching_id IN (SELECT id FROM precision_teachings WHERE workspace_id=?)''',
                (assignment['is_published'],classroom_id,assignment['teaching_id'],workspace_id))
        connection.execute('DELETE FROM classroom_archives WHERE classroom_id=? AND workspace_id=?',(classroom_id,workspace_id))
    write_audit('restore','classroom',classroom_id,{'workspace_id':workspace_id})
    return {'restored':True,'id':classroom_id}
