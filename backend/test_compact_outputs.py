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
