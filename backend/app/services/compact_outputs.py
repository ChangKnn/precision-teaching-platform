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

def class_transport_schema(schema, compact_large=False):
    result=deepcopy(schema)
    for key in ('class_summary','traceability'):
        result['required'].remove(key);result['properties'].pop(key)
    row=result['$defs']['levelAnalysis']
    for key in ('students','count'):
        row['required'].remove(key);row['properties'].pop(key)
    if compact_large:
        result['required'].remove('grouping_recommendations')
        result['properties'].pop('grouping_recommendations')
        evidence = result['$defs']['evidenceRef']
        for key in ('student_name', 'quote', 'interpretation'):
            evidence['required'].remove(key)
            evidence['properties'].pop(key)
        group = row['properties']['difficulty_groups']['items']
        group['required'].remove('difficulty')
        group['properties'].pop('difficulty')
    return result


def expand_large_class_output(value, payload):
    """Restore verified evidence and construct a single complete grouping locally."""
    result = deepcopy(value)
    students = {s['student']['student_id']: s for s in payload['student_diagnosis_results']}
    for level in result['level_analyses']:
        for difficulty in level.get('difficulty_groups', []):
            difficulty['difficulty'] = difficulty['problem_description']
        for evidence in level['typical_evidence']:
            student = students.get(evidence['student_id'])
            sources = (student or {}).get('diagnosis', {}).get('evidence', [])
            source = next((s for s in sources if s['ref'] == evidence['source_ref']), None)
            if not source:
                raise ValueError('典型证据未匹配学生个体诊断')
            evidence.update(student_name=student['student']['student_name'], quote=source['quote'], interpretation=source['interpretation'])
    if 'grouping_recommendations' not in result:
        groups, assigned = [], set()
        for level in result['level_analyses']:
            for difficulty in level.get('difficulty_groups', []):
                members = [sid for sid in difficulty['student_ids'] if sid not in assigned]
                assigned.update(members)
                if not members:
                    continue
                n = (len(members) + 5) // 6
                base, extra = divmod(len(members), n)
                pos = 0
                for index in range(n):
                    size = base + (index < extra)
                    ids = members[pos:pos + size]
                    pos += size
                    groups.append({'group_id': f'H{len(groups)+1}', 'group_name': f"{difficulty['problem_label']}组{index+1}",
                        'students': [{'student_id': sid, 'student_name': students[sid]['student']['student_name']} for sid in ids],
                        'common_characteristics': difficulty['problem_description'],
                        'progression_direction': difficulty['teaching_suggestion'], 'use_case': '教师可根据课堂情况调整。'})
        result['grouping_recommendations'] = {'homogeneous_groups': groups, 'heterogeneous_groups': [],
            'heterogeneous_not_recommended_reason': '请教师结合课堂任务自行决定是否采用异质分组。',
            'usage_note': '按层级和主要问题整理，供教师结合实际情况调整。'}
    return result


def _complete_nonoverlapping_groups(groups, source_ids):
    if not groups or any(not group.get('students') for group in groups):
        return False
    members = [student['student_id'] for group in groups for student in group['students']]
    return len(members) == len(set(members)) and set(members) == source_ids


def _repair_class_grouping(result, exact):
    """Replace unreliable rosters using verified input, never reuse wrong group descriptions."""
    grouping = result['grouping_recommendations']
    source_ids = {item['student_id'] for item in exact['level_assignments']}
    repaired = []
    if not _complete_nonoverlapping_groups(grouping['homogeneous_groups'], source_ids):
        analyses = {row['level']: row for row in result['level_analyses']}
        groups = []
        for level in exact['distribution']:
            students = level['students']
            if not students:
                continue
            # Spread a level evenly across groups of at most six, rather than
            # leaving an avoidable single student in the final group.
            n = (len(students) + 5) // 6
            base, extra = divmod(len(students), n)
            position = 0
            for index in range(n):
                size = base + (index < extra)
                members = students[position:position + size]
                position += size
                analysis = analyses.get(level['level'], {})
                groups.append({'group_id':f'H{len(groups)+1}', 'group_name':f"{level['level_name']}组{index+1}",
                    'students':members, 'common_characteristics':analysis.get('main_performance') or '依据当前诊断处于相同层级。',
                    'progression_direction':analysis.get('teaching_suggestion') or '结合学生的实际表现确定下一步支持。',
                    'use_case':'供教师结合课堂任务及学生实际情况调整。'})
        grouping['homogeneous_groups'] = groups
        repaired.append('同质分组按已有诊断层级自动整理，供教师结合实际情况调整。')
    heterogeneous = grouping['heterogeneous_groups']
    if heterogeneous and not _complete_nonoverlapping_groups(heterogeneous, source_ids):
        grouping['heterogeneous_groups'] = []
        grouping['heterogeneous_not_recommended_reason'] = '未形成完整可靠的异质分组名单，请教师结合学生情况自行安排。'
        repaired.append('异质分组名单需教师另行安排。')
    if repaired:
        grouping['usage_note'] = (grouping.get('usage_note') or '').strip() + ' ' + ' '.join(repaired)
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
    return _repair_class_grouping(result, exact)
