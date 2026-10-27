"""Store teacher-authored level descriptions without inventing an AI task analysis."""
from copy import deepcopy

from .ai import GENERAL_SOLO_ANCHORS

LEVELS = ('P', 'U', 'M', 'R', 'EA')
FIELDS = ('core_performance', 'decision_evidence', 'typical_expressions')


def teacher_rubric(task_text: str, levels: dict) -> dict:
    if set(levels) != set(LEVELS):
        raise ValueError('请保留 P、U、M、R、EA 五个层级')
    rows = {}
    for level in LEVELS:
        row = levels[level]
        if not isinstance(row, dict) or set(row) != set(FIELDS):
            raise ValueError('手填标准只接受主要认知特征、学生表现和示例三项')
        if any(not isinstance(row[field], list) or any(not isinstance(x, str) or len(x) > 10000 for x in row[field]) or len(row[field]) > 100 for field in FIELDS):
            raise ValueError('标准内容须为文本列表，请缩短过长内容')
        rows[level] = {'status': 'defined', **{field:[x.strip() for x in row[field] if x.strip()] for field in FIELDS},
                       'adjacent_boundaries': [], 'trace_to':[f'G-{level}']}
    return {
        'schema_version':'1.1',
        'source_basis':{'diagnostic_task_source':'教师当前诊断任务',
                        'general_rubric_source':'Skill 内置通用 SOLO 定义；具体标准由教师填写',
                        'general_rubric_anchors':deepcopy(GENERAL_SOLO_ANCHORS),
                        'student_responses_role':'not_provided',
                        'source_uncertainties':['教师手填标准，未由AI生成任务元素、关系或迁移分析；不得将未提供的结构视为已分析。']},
        'task_analysis':{
            'true_cognitive_demand':'具体任务要求以原文和教师填写的五级标准为准，未另作AI认知需求分析。',
            'elements':[{'id':'E1','name':'诊断任务原文（背景，不是已分解的认知要素）','meaning':task_text or '诊断任务尚未填写。'}],
            'relations':{'items':[],'whole_structure':'依教师各级标准判断；未提供独立的关系分析。','alternative_pathways':[]},
            'abstractions':[],
            'task_affordance':{'highest_reasonably_elicitable_level':next((level for level in reversed(LEVELS) if rows[level]['core_performance'] and rows[level]['decision_evidence']), 'P'),
                'limitations':['五级表由教师定义并确认；未独立审核任务能否引出各级表现，不得据此预设学生层级。'], 'revision_suggestions':[]}},
        'levels':rows,
        'usage_notes':{'evidence_sufficiency':['仅依据学生真实表达和已确认的教师标准判断，证据不足时明确说明。'],
                       'boundary_risks':['不得从手填表中未提供的任务分析或关系编号推断判断依据。'],
                       'downstream_instructions':['优先使用教师填写的 core_performance 与 decision_evidence；示例仅供参考，不是必需关键词。']}}


def require_complete_levels(rubric: dict) -> None:
    missing = [level for level in LEVELS if any(not any(str(x).strip() for x in rubric['levels'][level][field]) for field in FIELDS)]
    if missing:
        raise ValueError('请补全以下层级的主要认知特征和学生表现，并填写示例后再确认：' + '、'.join(missing))
