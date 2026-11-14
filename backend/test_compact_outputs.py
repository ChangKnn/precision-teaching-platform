import copy
import json
from unittest import TestCase
from backend.app.services.compact_outputs import RUBRIC_SCHEMA, expand_rubric, class_transport_schema, expand_class
from backend.app.services.ai import validate_task_rubric
from backend.app.services.skills import load_skill
from backend.app.services.class_report import validate_class_result
import jsonschema

class CompactOutputsTest(TestCase):
    def test_new_rubric_has_no_task_model_and_remains_downstream_valid(self):
        value={'levels':{level:{'core_performance':['具体任务特征'],'decision_evidence':['学生可观察表现及边界'],
                                      'typical_expressions':['我根据材料说明理由。']} for level in ('P','U','M','R','EA')},'notes':[]}
        jsonschema.validate(value,RUBRIC_SCHEMA)
        result=expand_rubric(value)
        self.assertNotIn('task_analysis',result)
        validate_task_rubric(load_skill('solo_task_rubric_builder'),result,require_complete=True)
        student=load_skill('solo_student_diagnosis_feedback')
        payload=json.loads((student.path.parent/'tests/sample-input.json').read_text(encoding='utf-8'))
        schema=json.loads((student.path.parent/'assets/diagnosis-input.schema.json').read_text(encoding='utf-8'))
        jsonschema.validate(payload,schema)  # Historical task analysis stays accepted.
        payload['task_specific_solo_rubric']=result
        jsonschema.validate(payload,schema)
        del payload['task_specific_solo_rubric']['levels']['EA']
        with self.assertRaises(jsonschema.ValidationError): jsonschema.validate(payload,schema)

    def test_class_model_omits_repeated_rosters_but_final_result_covers_everyone(self):
        skill=load_skill('solo_class_diagnosis_intervention')
        full=json.loads((skill.path.parent/'tests/sample-output.json').read_text())
        payload=json.loads((skill.path.parent/'tests/sample-input.json').read_text())
        schema=json.loads((skill.path.parent/'assets/class-diagnosis-output.schema.json').read_text())
        compact=copy.deepcopy(full)
        for key in ('class_summary','traceability'):compact.pop(key)
        for row in compact['level_analyses']:
            row.pop('count');row.pop('students')
        jsonschema.validate(compact,class_transport_schema(schema))
        result=expand_class(compact,payload)
        jsonschema.validate(result,schema);validate_class_result(result,payload)
        self.assertEqual(result['class_summary']['total_students'],len(payload['student_diagnosis_results']))
        self.assertLess(len(json.dumps(compact)),len(json.dumps(full)))

    def test_invalid_model_rosters_are_replaced_using_all_39_verified_students(self):
        skill=load_skill('solo_class_diagnosis_intervention')
        payload=json.loads((skill.path.parent/'tests/sample-input.json').read_text())
        output=json.loads((skill.path.parent/'tests/sample-output.json').read_text())
        schema=json.loads((skill.path.parent/'assets/class-diagnosis-output.schema.json').read_text())
        original=payload['student_diagnosis_results'][0]
        for index in range(35):
            student=copy.deepcopy(original)
            student['student']['student_id']=f'new-{index:02}'
            student['student']['student_name']=f'匿名{index:02}'
            payload['student_diagnosis_results'].append(student)
        for key in ('class_summary','traceability'): output.pop(key)
        for row in output['level_analyses']:
            row.pop('count');row.pop('students')
        grouping=output['grouping_recommendations']
        grouping['homogeneous_groups'][0]['students'].append(copy.deepcopy(grouping['homogeneous_groups'][0]['students'][0]))
        grouping['heterogeneous_groups'][0]['students'][0]['student_id']='unknown'
        result=expand_class(output,payload)
        jsonschema.validate(result,schema)
        validate_class_result(result,payload)
        groups=result['grouping_recommendations']
        ids=[s['student_id'] for group in groups['homogeneous_groups'] for s in group['students']]
        self.assertEqual(len(ids),39)
        self.assertEqual(len(set(ids)),39)
        self.assertTrue(all(len(group['students']) <= 6 for group in groups['homogeneous_groups']))
        self.assertIn('SOLO 层级',groups['usage_note'])
        self.assertEqual(groups['heterogeneous_groups'],[])
        self.assertIn('SOLO',groups['heterogeneous_not_recommended_reason'])

    def test_valid_model_grouping_is_preserved(self):
        skill=load_skill('solo_class_diagnosis_intervention')
        payload=json.loads((skill.path.parent/'tests/sample-input.json').read_text())
        result=json.loads((skill.path.parent/'tests/sample-output.json').read_text())
        original_groups = result['grouping_recommendations']['homogeneous_groups']
        original_groups[0]['students'] = [student for group in original_groups for student in group['students']]
        result['grouping_recommendations']['homogeneous_groups'] = original_groups[:1]
        expected=copy.deepcopy(result['grouping_recommendations'])
        for key in ('class_summary','traceability'):result.pop(key)
        for row in result['level_analyses']:row.pop('count');row.pop('students')
        expanded = expand_class(result,payload)
        groups = expanded['grouping_recommendations']['homogeneous_groups']
        levels = {student['student_id']: row['level'] for row in expanded['class_summary']['distribution'] for student in row['students']}
        self.assertTrue(all(len({levels[s['student_id']] for s in group['students']}) == 1 for group in groups))
