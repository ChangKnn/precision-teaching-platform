"""Export saved class feedback without invoking any model."""
from io import BytesIO
from html import escape

from .report_pdf import _markup, report_cjk_font
from .readable_evidence import readable_narrative, readable_student_names


def render_class_feedback_pdf(report, teaching, classroom, interaction=None):
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle, Spacer, CondPageBreak, KeepTogether
    except ImportError as error:
        raise RuntimeError('PDF 导出依赖未安装，请安装 reportlab') from error
    data = readable_narrative(readable_student_names(report['result']))
    font = report_cjk_font()
    teal, gray = colors.HexColor('#16574E'), colors.HexColor('#607470')
    styles = {key: ParagraphStyle(key, fontName=font, fontSize=size, leading=leading,
                                  textColor=teal if key in ('title','heading','sub') else colors.HexColor('#203330'),
                                  wordWrap='CJK', spaceAfter=6, keepWithNext=key in ('heading','sub'))
              for key, size, leading in [('title',20,28),('heading',14,21),('sub',11,17),('body',10,16),('small',9,14)]}
    flow, buffer = [], BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=17*mm, rightMargin=17*mm,
                            topMargin=16*mm, bottomMargin=17*mm, title=f"{teaching['title']}-班级集体反馈")
    def p(text, kind='body'):
        return Paragraph(_markup(text), styles[kind])
    def field(label, text):
        if text:
            flow.append(Paragraph(f'<font color="#16574E">{escape(label)}：</font>{_markup(text)}', styles['body']))
    def heading(text):
        flow.extend([CondPageBreak(75), Spacer(1,8), p(text,'heading')])
    def table(headers, rows, widths):
        cells=[[p(x,'small') for x in headers]]+[[p(x,'small') for x in row] for row in rows]
        t=Table(cells,colWidths=[doc.width*x for x in widths],repeatRows=1,splitInRow=1,hAlign='LEFT')
        t.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.4,colors.HexColor('#D5E2DC')),
                              ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#EFF6F3')),
                              ('VALIGN',(0,0),(-1,-1),'TOP'),
                              ('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),
                              ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
        flow.extend([t,Spacer(1,8)])
    names=lambda students:'、'.join(s['student_name'] for s in students)
    flow += [p('班级集体反馈','title'),p(teaching['title'],'sub'),
             p(f"{classroom['name']} · {teaching.get('course_name') or teaching.get('subject','')} · 纳入分析 {data['class_summary']['total_students']} 人",'small')]
    field('报告生成时间', report.get('generated_at'))
    if report.get('status') != 'ready':
        field('版本说明','当前导出为上次已保存报告，新一轮更新尚未完成。')
    heading('一、学生交互情况')
    if interaction:
        table(['已提交','纳入分析','最多发言','最少发言','平均发言'],
              [[f"{interaction['submitted']} 人",f"{data['class_summary']['total_students']} 人",
                f"{interaction['max']} 次",f"{interaction['min']} 次",f"{interaction['average']} 次"]],[.2]*5)
        flow.append(p('发言统计范围：本报告纳入且仍有会话记录的学生；按学生发言次数计算。','small'))
    else:
        field('纳入分析',f"{data['class_summary']['total_students']} 人；交互记录暂无可用统计。")
    heading('二、班级整体学情')
    table(['SOLO 层级','人数 / 比例','学生'],[
        [f"{r['level']} {r['level_name']}",f"{r['count']} 人 / {round(r['proportion']*100)}%",names(r['students']) or '无']
        for r in data['class_summary']['distribution']],[.23,.18,.59])
    flow.append(p('层级反映本次任务中的思维结构，不是固定能力标签。','small'))
    field('班级学情概览',data['overall_diagnosis']['overall_characteristics'])
    field('进阶方向',data['overall_diagnosis']['main_progression_direction'])
    for key,label in [('teaching_focus','教学重点'),('teaching_difficulty','教学难点')]:
        field(label,data['teaching_priorities'][key]['statement'])
    heading('三、学生分层学情')
    analyses={r['level']:r for r in data['level_analyses']}
    suggestions={r['level']:r for r in (report.get('teaching_suggestions') or {}).get('items',[])}
    for r in data['level_analyses']:
        flow += [CondPageBreak(60),p(f"{r['level_name']}（{r['count']} 人）",'sub')]
        field('当前表现',r['main_performance'])
        problems=r.get('difficulty_groups',[]) if data.get('problem_grouping_version')=='1.1' else []
        field('主要困难',problems[0]['problem_description'] if problems else '；'.join(r.get('main_obstacles',[])))
        d=r.get('teaching_design') or suggestions.get(r['level']) or {}
        for key,label in [('content','教学建议 · 内容'),('task','教学建议 · 任务'),('scaffold','教学建议 · 支架')]:
            field(label,d.get(key) or (d.get('activity') if key=='task' else ''))
        if not d:
            field('教学建议',r.get('teaching_suggestion'))
        if len(problems)>1:
            field('其他困难',problems[1]['problem_description'])
            field('针对性建议',problems[1]['teaching_suggestion'])
        field('学生',names(r['students']))
        for case in r.get('student_cases',[]):
            field(case['student_name'],case['level_reason']+(' 主要障碍：'+case['obstacle'] if case.get('obstacle') else ''))
    heading('四、教学干预建议')
    flow.append(p('全班教学建议','sub'))
    field('进阶方向',data['overall_diagnosis']['main_progression_direction'])
    field('教学重点',data['teaching_priorities']['teaching_focus']['statement'])
    flow.append(p('小组教学建议','sub'))
    for index,g in enumerate(data['grouping_recommendations']['homogeneous_groups'],1):
        ids={s['student_id'] for s in g['students']}
        row=next((r for r in data['class_summary']['distribution'] if ids <= {s['student_id'] for s in r['students']}),{})
        start = len(flow)
        flow += [p(f"第{index}组 · {g['group_name']}",'sub')]
        field('学情',g['common_characteristics'])
        field('学习任务',analyses.get(row.get('level'),{}).get('teaching_design',{}).get('task'))
        field('教学支持',g['progression_direction'])
        field('学生',names(g['students']))
        block = flow[start:]
        flow[start:] = [KeepTogether(block)]
    flow.append(p('个体关注建议','sub'))
    field('核对提示',data.get('individual_attention_warning'))
    for a in data.get('individual_attention',[]):
        lookup={s['student_id']:s['student_name'] for r in data['class_summary']['distribution'] for s in r['students']}
        uncertain=a['category']=='情绪表达'
        field(lookup.get(a['student_id'],'学生')+' · '+('学生表达（待教师核对）' if uncertain else a['category']),
              '请结合学生原话了解其需要，避免仅凭对话推断情绪。' if uncertain else a['observation'])
        field('依据',a['evidence_quote'])
        field('建议',a['teacher_action'])
    if not data.get('individual_attention') and not data.get('individual_attention_warning'):
        flow.append(p('本次未记录需要特别提醒的情况，请结合实际持续关注。'))
    def footer(canvas, doc):
        canvas.saveState();canvas.setFont(font,8);canvas.setFillColor(gray)
        canvas.drawCentredString(A4[0]/2,9*mm,f'班级集体反馈 · {doc.page}')
        canvas.restoreState()
    try:
        doc.build(flow,onFirstPage=footer,onLaterPages=footer)
    except Exception as error:
        raise RuntimeError('班级反馈 PDF 排版失败，请稍后重试') from error
    return buffer.getvalue()
