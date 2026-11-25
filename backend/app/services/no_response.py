"""A submitted session without student text is recorded locally as P, never given invented evidence."""
import hashlib
import json
from ..database import database, fetch_one, utc_now


def is_no_response(result):
    return result.get('no_response') is True and result.get('diagnosis', {}).get('level') == 'P' and not result['diagnosis'].get('evidence')


def record_no_response(session_id, payload, context, skill, lightweight):
    stamp = utc_now()
    input_hash = hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    explanation = '已提交但未发言，也未提交文字作答，按平台规则记为前结构；暂无可分析的思维证据，不能据此推断学生的实际能力或具体困难。'
    result = {
        'schema_version':'1.0','no_response':True,'student':payload['student'],
        'evidence_sources':{'dialogue_used':False,'submitted_text_used':False},
        'diagnosis':{'level':'P','level_name':'前结构','rationale':explanation,'next_level_gap':'尚需学生围绕任务表达一个想法或依据。','evidence':[],'multi_source_note':'无学生发言或文字作答；若上传了作答图片，请教师结合图片核对。'},
        'thinking_structure_summary':'暂无学生表达，未观察到可分析的思维结构。',
        'progression':{'current_level':'P','target_level':'U','main_obstacle':'尚未提供作答，具体困难有待了解。','concrete_goal':'先围绕任务说出一个想法或疑问，并说明理由。'},
        'strategies':[{'title':'补充表达','action':'用一句话说出你对任务的初步想法或疑问。','transfer_use':'开始学习任务时'},
                      {'title':'提供依据','action':'从任务中选择一个条件或材料，说明你为什么这样想。','transfer_use':'说明自己的判断时'}],
        'traceability':{'rubric_level':'P','rubric_trace_ids':['G-P'],'student_evidence_refs':[]}}
    teacher_text = f'### SOLO 水平\n前结构\n\n### 判断依据\n{explanation}\n\n### 当前障碍\n尚未提供作答，具体困难有待了解。'
    feedback = '### 本次学习记录\n你已提交任务，但还没有留下自己的发言或文字作答，目前还无法了解你的思考过程。\n\n### 下一步目标\n围绕任务说出一个想法或疑问。\n\n### 学习建议\n先用一句话表达想法，再选一个条件或材料说明理由。'
    with database() as c:
        c.execute('''INSERT INTO student_diagnosis_reports
            (session_id,teaching_id,skill_key,skill_version,provider,status,input_hash,generated_json,report_text,student_feedback_text,generated_at,updated_at,report_kind)
            VALUES (?,?,?,?,'rule','draft',?,?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET
            provider='rule',status='draft',input_hash=excluded.input_hash,generated_json=excluded.generated_json,
            report_text=excluded.report_text,student_feedback_text=excluded.student_feedback_text,
            generated_at=excluded.generated_at,updated_at=excluded.updated_at,reviewed_at=NULL,pushed_at=NULL,report_kind=excluded.report_kind''',
            (session_id,context['teaching_id'],skill.key,skill.version,input_hash,json.dumps(result,ensure_ascii=False),teacher_text,feedback,stamp,stamp,'brief' if lightweight else 'detailed'))
    from ..student_api import serialize_student_report, refresh_feedback_counts
    refresh_feedback_counts(context['teaching_id'])
    classroom=fetch_one('SELECT classroom_id FROM students WHERE id=?',(context['student_id'],))
    if classroom:
        from ..learning_feedback import sync_automatic_learning
        sync_automatic_learning(context['teaching_id'],classroom['classroom_id'])
    return {'report':serialize_student_report(session_id)}
