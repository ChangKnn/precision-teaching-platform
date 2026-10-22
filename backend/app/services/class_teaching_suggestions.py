"""Short, source-bound teaching ideas, kept separate from class diagnosis."""
from __future__ import annotations

import json
from uuid import uuid4

import jsonschema
from fastapi import APIRouter, HTTPException, Request

from ..config import settings
from ..database import database, fetch_one, utc_now, write_audit
from .ai import _run_structured_skill
from .skills import load_skill

router = APIRouter(prefix='/api')
KEY = 'class_teaching_suggestions'
LEVELS = ('P', 'U', 'M', 'R', 'EA')
TARGETS = dict(zip(LEVELS, ('U', 'M', 'R', 'EA', 'higher_EA')))
FIELDS = ('development_goal', 'content', 'activity', 'scaffold')


def latest_suggestions(teaching_id: str, classroom_id: str, source_hash: str) -> dict | None:
    row = fetch_one(
        """SELECT output_json, input_json, provider, skill_version, completed_at FROM ai_jobs
           WHERE skill_key = ? AND status = 'completed' AND skill_version = ?
             AND json_extract(input_json, '$.teaching_id') = ?
             AND json_extract(input_json, '$.classroom_id') = ?
             AND json_extract(input_json, '$.source_hash') = ?
           ORDER BY completed_at DESC LIMIT 1""",
        (KEY, load_skill(KEY).version, teaching_id, classroom_id, source_hash),
    )
    if not row:
        return None
    result = json.loads(row['output_json'])
    counts = {need['level']: need['count'] for need in json.loads(row['input_json'])['level_needs']}
    return {**result, 'items': [{**item, 'count': counts[item['level']]} for item in result['items']],
            'provider': row['provider'], 'generated_at': row['completed_at'], 'skill_version': row['skill_version']}


def build_input(teaching_id: str, classroom_id: str) -> dict:
    from .class_report import class_report_input
    payload, source_hash, _ = class_report_input(teaching_id, classroom_id)
    row = fetch_one('SELECT status, source_hash FROM class_diagnosis_reports WHERE teaching_id = ? AND classroom_id = ?', (teaching_id, classroom_id))
    if not payload or not row or row['status'] != 'ready' or row['source_hash'] != source_hash:
        raise HTTPException(status_code=409, detail='请先完成当前班级诊断，再生成教学建议')
    context = payload['precision_teaching_context']
    if not context['precision_teaching_topic'].strip() or not any(goal.strip() for goal in context['precision_teaching_goals']):
        raise HTTPException(status_code=409, detail='请先填写精准教学主题和目标')
    needs = []
    for level in LEVELS:
        students = [item for item in payload['student_diagnosis_results'] if item['diagnosis']['level'] == level]
        if not students:
            continue
        needs.append({
            'level': level, 'target_level': TARGETS[level], 'count': len(students),
            'observed_obstacles': list(dict.fromkeys(item['progression']['main_obstacle'] for item in students)),
            'progression_goals': list(dict.fromkeys(item['progression']['concrete_goal'] for item in students)),
        })
    return {'teaching_id': teaching_id, 'classroom_id': classroom_id, 'source_hash': source_hash,
            'precision_teaching_context': context, 'level_needs': needs}


def validate_suggestions(result: dict, payload: dict) -> None:
    skill = load_skill(KEY)
    schema = json.loads((skill.path.parent/'assets/teaching-suggestions.schema.json').read_text(encoding='utf-8'))
    jsonschema.validate(result, schema)
    actual = [item['level'] for item in result['items']]
    expected = [item['level'] for item in payload['level_needs']]
    if len(actual) != len(set(actual)) or set(actual) != set(expected):
        raise ValueError('教学建议必须恰好覆盖本班实际存在的层级，不得重复或遗漏')
    for item in result['items']:
        if item['target_level'] != TARGETS[item['level']]:
            raise ValueError('教学建议的发展目标须为相邻进阶，不得重新定级或跳级')
        if not all(item[key].strip() for key in FIELDS):
            raise ValueError('教学建议的四个栏目均需有简明内容')
        if sum(len(item[key]) for key in FIELDS) > 180:
            raise ValueError('教学建议过长，请将每层四项合计压缩到180字以内')
    result['items'].sort(key=lambda item: LEVELS.index(item['level']))


@router.post('/precision-teachings/{teaching_id}/classrooms/{classroom_id}/teaching-suggestions/generate')
def generate_teaching_suggestions(teaching_id: str, classroom_id: str, request: Request) -> dict:
    payload = build_input(teaching_id, classroom_id)
    if settings.ai_provider == 'mock':
        raise HTTPException(status_code=409, detail='教学建议需要配置真实AI，不使用模拟结果')
    skill = load_skill(KEY)
    job_id, now = f'job-{uuid4().hex}', utc_now()
    with database() as connection:
        connection.execute(
            """INSERT INTO ai_jobs (id,workspace_id,skill_key,skill_version,provider,status,input_json,created_at)
               VALUES (?,?,?,?,?,'running',?,?)""",
            (job_id, request.state.teacher_workspace_id, KEY, skill.version, settings.ai_provider, json.dumps(payload,ensure_ascii=False), now),
        )
    try:
        result = _run_structured_skill(skill, payload, 'teaching-suggestions.schema.json', KEY, settings.ai_provider)
        validate_suggestions(result, payload)
        current = build_input(teaching_id, classroom_id)
        if current['source_hash'] != payload['source_hash']:
            raise HTTPException(status_code=409, detail='生成期间班级诊断或教学信息已变化，请根据最新内容重新生成')
        with database() as connection:
            connection.execute("UPDATE ai_jobs SET status='completed', output_json=?, completed_at=? WHERE id=?", (json.dumps(result,ensure_ascii=False),utc_now(),job_id))
        write_audit('generate',KEY,teaching_id,{'classroom_id':classroom_id,'job_id':job_id})
        return {'job_id': job_id, 'teaching_suggestions': latest_suggestions(teaching_id,classroom_id,payload['source_hash'])}
    except Exception as error:
        with database() as connection:
            connection.execute("UPDATE ai_jobs SET status='failed', error=?, completed_at=? WHERE id=?", (str(error),utc_now(),job_id))
        if isinstance(error, HTTPException):
            raise
        raise HTTPException(status_code=502, detail=f'教学建议生成失败：{error}') from error
