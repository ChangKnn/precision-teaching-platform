"""A theme owns independent task contexts; legacy task/session IDs stay intact."""
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from .database import database, fetch_one, fetch_all, utc_now

router = APIRouter(prefix='/api/precision-teachings')
SHARED_FIELDS = ('title','goal','content','rationale','subject','grade','textbook',
                 'estimated_periods','education_stage','course_name','major')


class TaskName(BaseModel):
    name: str = Field(min_length=1, max_length=120)

    @field_validator('name')
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('请填写诊断任务名称')
        return value.strip()


def theme_id(teaching: dict) -> str:
    return teaching.get('parent_teaching_id') or teaching['id']


def family_tasks(teaching_id: str) -> list[dict]:
    teaching = fetch_one('SELECT * FROM precision_teachings WHERE id=?', (teaching_id,))
    if not teaching:
        raise HTTPException(status_code=404, detail='精准教学不存在')
    root = theme_id(teaching)
    return fetch_all('''SELECT p.id,p.parent_teaching_id,p.task_name,p.task_order,
        p.title,p.workspace_id,p.grade,p.subject,p.course_name,p.education_stage,p.status,
        COALESCE(d.status,'draft') AS diagnosis_status,
        (SELECT COUNT(*) FROM student_task_sessions s WHERE s.teaching_id=p.id AND s.status='submitted') AS submitted_count
        FROM precision_teachings p LEFT JOIN diagnosis_tasks d ON d.teaching_id=p.id
        WHERE p.id=? OR p.parent_teaching_id=? ORDER BY p.task_order,p.created_at,p.id''', (root,root))


def _owned(teaching_id: str, request: Request) -> dict:
    teaching = fetch_one('SELECT * FROM precision_teachings WHERE id=? AND workspace_id=?',
                         (teaching_id,request.state.teacher_workspace_id))
    if not teaching:
        raise HTTPException(status_code=404, detail='精准教学不存在')
    return teaching


@router.get('/{teaching_id}/diagnostic-tasks')
def list_tasks(teaching_id: str, request: Request) -> dict:
    teaching = _owned(teaching_id,request)
    return {'theme_id':theme_id(teaching),'tasks':family_tasks(teaching_id)}


@router.post('/{teaching_id}/diagnostic-tasks', status_code=201)
def create_task(teaching_id: str, payload: TaskName, request: Request) -> dict:
    teaching = _owned(teaching_id,request)
    root = theme_id(teaching)
    now = utc_now()
    task_id = 'pt-task-'+uuid4().hex[:16]
    with database() as connection:
        connection.execute('BEGIN IMMEDIATE')
        source = connection.execute('SELECT * FROM precision_teachings WHERE id=?',(root,)).fetchone()
        order = connection.execute('SELECT COALESCE(MAX(task_order),0)+1 AS n FROM precision_teachings WHERE id=? OR parent_teaching_id=?',(root,root)).fetchone()['n']
        columns = ('id','workspace_id','parent_teaching_id','task_name','task_order',*SHARED_FIELDS,'status','current_stage','created_at','updated_at')
        values = (task_id,source['workspace_id'],root,payload.name,order,
                  *(source[k] for k in SHARED_FIELDS),'draft','diagnosis',now,now)
        connection.execute('INSERT INTO precision_teachings ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')',values)
        connection.execute('''INSERT INTO diagnosis_tasks (teaching_id,goal,task_text,ai_role,status,updated_at)
            VALUES (?,?,'','','draft',?)''',(task_id,source['goal'],now))
        connection.execute("UPDATE precision_teachings SET task_name='诊断任务一' WHERE id=? AND task_name=''",(root,))
    return {'theme_id':root,'task_id':task_id,'tasks':family_tasks(root)}


@router.put('/{teaching_id}/diagnostic-task-name')
def rename_task(teaching_id: str, payload: TaskName, request: Request) -> dict:
    teaching = _owned(teaching_id,request)
    with database() as connection:
        connection.execute('UPDATE precision_teachings SET task_name=?,updated_at=? WHERE id=?',
                           (payload.name,utc_now(),teaching_id))
    return {'theme_id':theme_id(teaching),'task_id':teaching_id,'tasks':family_tasks(teaching_id)}
