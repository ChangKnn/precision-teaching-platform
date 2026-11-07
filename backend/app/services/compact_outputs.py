"""Small model contracts with deterministic platform metadata."""
from copy import deepcopy

LEVELS = ('P','U','M','R','EA')
FIELDS = ('core_performance','decision_evidence','typical_expressions')
RUBRIC_SCHEMA = {'type':'object','additionalProperties':False,'required':['levels','notes'],
    'properties':{
        'levels':{'type':'object','additionalProperties':False,'required':list(LEVELS),
            'properties':{level:{'type':'object','additionalProperties':False,'required':list(FIELDS),
                'properties':{field:{'type':'array','minItems':1,'items':{'type':'string','minLength':1}} for field in FIELDS}} for level in LEVELS}},
        'notes':{'type':'array','items':{'type':'string'}}}}

def expand_rubric(value):
    if 'schema_version' in value: return value  # Historical fixtures and saved records.
    from .ai import GENERAL_SOLO_ANCHORS
    return {'schema_version':'1.1',
        'source_basis':{'diagnostic_task_source':'当前诊断任务', 'general_rubric_source':'内置通用SOLO标准',
                        'general_rubric_anchors':deepcopy(GENERAL_SOLO_ANCHORS)},
        'levels':{level:{**value['levels'][level],'status':'defined','adjacent_boundaries':[],
                         'trace_to':[f'G-{level}']} for level in LEVELS},
        'usage_notes':{'evidence_sufficiency':['仅依据学生自主表达判断，不把示例视为唯一答案。'],'boundary_risks':value['notes'],
                       'downstream_instructions':['以学生表现中的相邻层级边界判断，接纳不同合理解法，不按字数或术语数量定级。']}}

def class_transport_schema(schema):
    result=deepcopy(schema)
    for key in ('class_summary','traceability'):
        result['required'].remove(key);result['properties'].pop(key)
    row=result['$defs']['levelAnalysis']
    for key in ('students','count'):
        row['required'].remove(key);row['properties'].pop(key)
    return result

def expand_class(value,payload):
    from .class_report import deterministic_distribution
    result=deepcopy(value)
    exact=deterministic_distribution(payload)
    analyses={row['level']:row for row in result['level_analyses']}
    distribution=[]
    for row in exact['distribution']:
        analysis=analyses.get(row['level'])
        meaning=analysis['level_meaning'] if analysis else '本次提交中没有该层级学生'
        distribution.append({**row,'level_meaning':meaning})
        if analysis:
            analysis['students']=row['students'];analysis['count']=row['count']
    result['class_summary']={'total_students':exact['total_students'],'distribution':distribution}
    result['traceability']={'source_skill':'solo-student-diagnosis-feedback','input_student_count':exact['total_students'],
        'output_student_ids':[s['student_id'] for s in exact['level_assignments']],
        'level_assignments':exact['level_assignments'],'levels_unchanged':True,
        'teaching_context_fields_used':list(payload['precision_teaching_context'])}
    return result
