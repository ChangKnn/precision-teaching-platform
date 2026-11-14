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

def class_transport_schema(schema, compact_large=False, separate_problems=False):
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
    if separate_problems:
        row['properties'].pop('difficulty_groups', None)
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
                    name = difficulty.get('group_name') or f'第{len(groups)+1}学习小组'
                    if n > 1 and difficulty.get('group_name'):
                        name = f'{name}{index+1}'
                    groups.append({'group_id': f'H{len(groups)+1}', 'group_name': name,
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


def practical_grouping(result):
    """Make existing group proposals easier to use without inventing student needs."""
    view = deepcopy(result)
    grouping = view.get('grouping_recommendations') or {}
    distribution = view.get('class_summary', {}).get('distribution', [])
    levels = {s['student_id']: row['level'] for row in distribution for s in row.get('students', [])}
    source_ids = set(levels)
    changed = False
    for key in ('homogeneous_groups', 'heterogeneous_groups'):
        original = grouping.get(key, [])
        if not _complete_nonoverlapping_groups(original, source_ids):
            continue
        if not any(len(g['students']) < 3 for g in original) or len(source_ids) < 2:
            continue
        buckets = [{'entries': [(student, index) for student in group['students']]} for index, group in enumerate(original)]
        # Prefer students at the same or adjacent level, while preserving all IDs.
        def distance(left, right):
            return min(abs(LEVELS.index(levels[a['student_id']]) - LEVELS.index(levels[b['student_id']]))
                       for a, _ in left['entries'] for b, _ in right['entries'])
        while True:
            small = next((b for b in buckets if len(b['entries']) < 3), None)
            if small is None or len(buckets) == 1:
                break
            candidates = [b for b in buckets if b is not small and len(b['entries']) + len(small['entries']) <= 6]
            if candidates:
                target = min(candidates, key=lambda b: (distance(small, b), len(b['entries'])))
                target['entries'].extend(small['entries'])
                buckets.remove(small)
                continue
            donors = [b for b in buckets if b is not small and len(b['entries']) > 3]
            if not donors:
                break
            donor = min(donors, key=lambda b: (distance(small, b), -len(b['entries'])))
            small['entries'].append(donor['entries'].pop())
        adapted = []
        for index, bucket in enumerate(buckets, 1):
            entries = bucket['entries']
            origin_ids = list(dict.fromkeys(origin for _, origin in entries))
            group = deepcopy(original[origin_ids[0]])
            group['group_id'] = f"{'H' if key == 'homogeneous_groups' else 'X'}{index}"
            group['students'] = [student for student, _ in entries]
            if len(origin_ids) > 1:
                group['group_name'] = f'第{index}学习小组'
                if key == 'homogeneous_groups':
                    group['common_characteristics'] = '本组由不同学习需要的学生组成，请按具体需要提供支持。'
                    directions = list(dict.fromkeys(original[i]['progression_direction'].strip() for i in origin_ids if original[i]['progression_direction'].strip()))
                    group['progression_direction'] = '；'.join(directions)
                    group['use_case'] = '共同任务中按学生需要提供差异化支持，教师可调整分组。'
                else:
                    group['grouping_rationale'] = '为便于课堂组织合并，成员的学习需要可能不同。'
                    group['collaboration_direction'] = '先独立思考，再围绕共同任务交流；教师按需要支持。'
                    group['shared_product'] = '由教师结合共同任务确定。'
            adapted.append(group)
        grouping[key] = adapted
        changed = True
    if changed:
        note = '为便于课堂组织，已优先合并人数较少的小组；合并后请根据学生的具体需要提供支持。'
        grouping['usage_note'] = (grouping.get('usage_note') or '').strip() + ' ' + note
    return view


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
                groups.append({'group_id':f'H{len(groups)+1}', 'group_name':f"第{len(groups)+1}学习小组",
                    'students':members, 'common_characteristics':analysis.get('main_performance') or '依据当前诊断处于相同层级。',
                    'progression_direction':analysis.get('teaching_design', {}).get('task') or analysis.get('teaching_suggestion') or '结合学生的实际表现确定下一步支持。',
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
    return practical_grouping(_repair_class_grouping(result, exact))
