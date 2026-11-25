import copy
import json
from unittest import TestCase
from unittest.mock import patch
from fastapi import BackgroundTasks
from jsonschema import validate
from backend import test_task_version_runtime as fixtures
from backend.app import student_api, database as db
from backend.app.services import class_report, compact_outputs
from backend.app.services.skills import load_skill

class NoResponseDiagnosisTest(TestCase):
    def setUp(self):
        self.case=fixtures.TaskVersionRuntimeTest('test_unchanged_publish_does_not_create_spurious_versions')
        self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.session=self.case.start()['session']

    def test_empty_submission_is_p_without_model_or_invented_evidence_and_can_aggregate(self):
        student_api.submit_task(self.session['id'],student_api.StudentTaskSubmit(),self.case.student)
        with patch.object(student_api,'run_student_diagnosis_report',side_effect=AssertionError('no model needed')):
            result=student_api.generate_student_report(self.session['id'],BackgroundTasks(),lightweight=True)['report']['result']
        self.assertEqual(result['diagnosis']['level'],'P')
        self.assertEqual(result['diagnosis']['evidence'],[])
        self.assertTrue(result['no_response'])
        self.assertIn('未发言',result['diagnosis']['rationale'])
        payload,_,_=class_report.class_report_input('pt','class')
        self.assertEqual(len(payload['student_diagnosis_results']),1)
        skill=load_skill('solo_class_diagnosis_intervention')
        validate(payload,json.loads((skill.path.parent/'assets/class-diagnosis-input.schema.json').read_text()))
        output=json.loads((skill.path.parent/'tests/sample-output.json').read_text())
        output['level_analyses']=[copy.deepcopy(next(i for i in output['level_analyses'] if i['level']=='P'))]
        output['level_analyses'][0]['typical_evidence']=[]
        compact_outputs._restore_typical_evidence(output,payload)
        self.assertEqual(output['level_analyses'][0]['typical_evidence'],[])
        output['individual_attention']=[]
        output['grouping_recommendations']['homogeneous_groups']=[]
        output['grouping_recommendations']['heterogeneous_groups']=[]
        output['grouping_recommendations']['heterogeneous_not_recommended_reason']='先完成基本表达。'
        output=compact_outputs.expand_class(output,payload)
        class_report.validate_class_result(output,payload)
        validate(output,json.loads((skill.path.parent/'assets/class-diagnosis-output.schema.json').read_text()))
        self.assertEqual(output['class_summary']['total_students'],1)
        # Evidence fallback stays strict for ordinary students without the no-response flag.
        invalid=copy.deepcopy(payload);invalid['student_diagnosis_results'][0].pop('no_response')
        with self.assertRaises(Exception):
            validate(invalid,json.loads((skill.path.parent/'assets/class-diagnosis-input.schema.json').read_text()))

    def test_text_submission_without_dialogue_is_still_analyzed_normally(self):
        student_api.submit_task(self.session['id'],student_api.StudentTaskSubmit(submitted_text='我认为需要比较两个条件。'),self.case.student)
        payload,_=student_api.build_student_diagnosis_input(self.session['id'])
        skill=load_skill('solo_student_diagnosis_feedback')
        validate(payload,json.loads((skill.path.parent/'assets/diagnosis-input.schema.json').read_text()))
        self.assertEqual(payload['submitted_text'],'我认为需要比较两个条件。')
