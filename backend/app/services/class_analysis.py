"""Prepare every submitted student's cached analysis before class aggregation."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock, RLock
import hashlib
import json
from fastapi import BackgroundTasks, HTTPException
from ..database import database, fetch_all, fetch_one, utc_now

_lock = Lock()
_active = set()
_student_locks = {}

def student_lock(session_id):
    with _lock:
        return _student_locks.setdefault(session_id,RLock())

def submitted(teaching_id, classroom_id):
    return fetch_all('''SELECT sts.id,s.name FROM student_task_sessions sts JOIN students s ON s.id=sts.student_id
        WHERE sts.teaching_id=? AND s.classroom_id=? AND sts.status='submitted' ORDER BY sts.id''', (teaching_id,classroom_id))

def prepare_class(teaching_id, classroom_id, background):
    key = (teaching_id,classroom_id)
    rows = submitted(*key)
    if not rows:
        raise HTTPException(409,'该班级还没有已提交的对话')
    with _lock:
        if key in _active: return {'report': {'status':'preparing'}}
        _active.add(key)
    with database() as c:
        c.execute('''INSERT INTO class_diagnosis_reports
            (teaching_id,classroom_id,skill_key,skill_version,provider,status,source_hash,updated_at)
            VALUES (?,?,'solo_class_diagnosis_intervention','preparing','pending','preparing','preparing',?)
            ON CONFLICT(teaching_id,classroom_id) DO UPDATE SET status='preparing',error=NULL,updated_at=excluded.updated_at''',
            (*key,utc_now()))
    background.add_task(analyze_class,teaching_id,classroom_id)
    return {'report':{'status':'preparing'}}

def analyze_class(teaching_id, classroom_id):
    from .. import student_api as api
    from .class_report import queue_class_report
    key = (teaching_id,classroom_id)
    failures=[]
    def analyze(row):
        payload,_ = api.build_student_diagnosis_input(row['id'])
        digest=hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        cached=fetch_one('SELECT * FROM student_diagnosis_reports WHERE session_id=?',(row['id'],))
        if cached and cached['input_hash']==digest and cached['status']!='stale' and cached['provider']!='mock':return
        api.generate_student_report(row['id'],BackgroundTasks(),lightweight=True)
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs={pool.submit(analyze,row):row['name'] for row in submitted(*key)}
            for job in as_completed(jobs):
                try:job.result()
                except Exception as error:
                    causes = []
                    current = error
                    while current is not None and len(causes) < 6:
                        causes.append(str(getattr(current, 'detail', current)))
                        current = current.__cause__
                    detail = ' '.join(causes)
                    reason = ('没有学生发言' if '至少需要一条学生发言' in detail else
                              '原话引用未通过核验' if '证据quote' in detail else
                              '分析输入或量规格式有误' if '学生报告输入校验失败' in detail else
                              '模型或网络处理失败')
                    failures.append(f'{jobs[job]}（{reason}）')
        if failures:
            raise ValueError(f'{len(failures)}名学生分析未完成：'+ '、'.join(failures) +'。请检查并重试；成功结果已保留，尚未生成不完整班级报告')
        background=BackgroundTasks()
        if not queue_class_report(*key,background,force=True):
            raise ValueError('已提交学生尚未全部完成分析，请重试')
        for task in background.tasks:task.func(*task.args,**task.kwargs)
    except Exception as error:
        with database() as c:
            c.execute("UPDATE class_diagnosis_reports SET status='failed',error=?,updated_at=? WHERE teaching_id=? AND classroom_id=?",(str(error)[:1000],utc_now(),*key))
    finally:
        with _lock:_active.discard(key)
