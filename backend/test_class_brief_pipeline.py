import json
from dataclasses import replace
from unittest import TestCase
from unittest.mock import patch
from fastapi import BackgroundTasks, HTTPException
from backend import test_task_version_runtime as fixtures
from backend.app import database as db, student_api as api
from backend.app.schemas import StudentTaskSubmit, StudentMessageCreate
from backend.app.services import class_analysis as pipeline
from backend.app.services.class_report import class_report_input
from backend.app.services.skills import load_skill

class ClassBriefPipelineTest(TestCase):
    def setUp(self):
        self.fixture=fixtures.TaskVersionRuntimeTest('test_unchanged_publish_does_not_create_spurious_versions')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.session=self.fixture.start()['session']['id'];self.fixture.send(self.session)
        api.submit_task(self.session,StudentTaskSubmit(submitted_text='我的解释'),self.fixture.student)
        now=db.utc_now()
        with db.database() as c:
            c.execute("INSERT INTO students VALUES ('second','class','测试学校','测试班','另一位学生',?,?)",(now,now))
        second=db.fetch_one("SELECT * FROM students WHERE id='second'")
        self.second=api.start_task('pt',second)['session']['id']
        api.create_message(self.second,StudentMessageCreate(content='我认为应比较条件，并解释不同条件如何影响判断。'),second)
        api.submit_task(self.second,StudentTaskSubmit(submitted_text='第二位学生的解释'),second)
        skill=load_skill('solo_student_diagnosis_feedback')
        self.sample=json.loads((skill.path.parent/'tests/sample-output.json').read_text())
        p=patch.object(api,'settings',replace(api.settings,ai_provider='openrouter'))
        p.start();self.addCleanup(p.stop)

    def brief(self,skill,payload,provider):
        value=json.loads(json.dumps(self.sample));value['student']=payload['student']
        return value,'简短依据与障碍',''

    def generate(self,session):
        with patch('backend.app.services.ai.run_student_brief',side_effect=self.brief):
            return api.generate_student_report(session,BackgroundTasks(),lightweight=True)

    def test_one_report_cannot_stand_for_two_submitted_students(self):
        self.generate(self.session)
        self.assertIsNone(class_report_input('pt','class')[0])
        self.generate(self.second)
        self.assertEqual(len(class_report_input('pt','class')[0]['student_diagnosis_results']),2)

    def test_preparation_reuses_cached_analysis_and_only_completes_missing(self):
        self.generate(self.session)
        with patch('backend.app.services.ai.run_student_brief',side_effect=self.brief) as model,patch('backend.app.services.class_report.queue_class_report',return_value={'status':'ready'}) as queue:
            pipeline.analyze_class('pt','class')
            self.assertEqual(model.call_count,1);self.assertEqual(queue.call_count,1)
        self.assertEqual(api.serialize_student_report(self.second)['report_kind'],'brief')

    def test_compact_rubric_supports_individual_and_all_students_in_class(self):
        rubric=json.loads(db.fetch_one("SELECT current_json FROM diagnosis_rubrics WHERE teaching_id='pt'")['current_json'])
        rubric.pop('task_analysis',None)
        with db.database() as c:
            c.execute("UPDATE diagnosis_rubrics SET current_json=? WHERE teaching_id='pt'",(json.dumps(rubric,ensure_ascii=False),))
        with patch.object(api,'run_student_diagnosis_report',return_value=(self.sample,'教师报告','学生反馈')),patch.object(api,'queue_class_report'):
            result=api.generate_student_report(self.session,BackgroundTasks())
        self.assertEqual(result['report']['report_kind'],'detailed')
        with patch('backend.app.services.ai.run_student_brief',side_effect=self.brief) as model, patch('backend.app.services.class_report.queue_class_report',return_value={'status':'ready'}) as queue:
            pipeline.analyze_class('pt','class')
            self.assertEqual(model.call_count,1)
            self.assertEqual(queue.call_count,1)
        self.assertEqual(len(class_report_input('pt','class')[0]['student_diagnosis_results']),2)

    def test_invalid_rubric_error_does_not_expose_student_input(self):
        rubric=json.loads(db.fetch_one("SELECT current_json FROM diagnosis_rubrics WHERE teaching_id='pt'")['current_json'])
        del rubric['levels']['EA']
        with db.database() as c:
            c.execute("UPDATE diagnosis_rubrics SET current_json=? WHERE teaching_id='pt'",(json.dumps(rubric,ensure_ascii=False),))
        with self.assertRaises(HTTPException) as error:
            api.generate_student_report(self.session,BackgroundTasks())
        self.assertIn('EA',error.exception.detail)
        self.assertNotIn('我的解释',error.exception.detail)

    def test_failed_student_blocks_aggregation_and_keeps_successes(self):
        self.generate(self.session)
        background=BackgroundTasks();pipeline.prepare_class('pt','class',background)
        with patch('backend.app.services.ai.run_student_brief',side_effect=RuntimeError('failed')),patch('backend.app.services.class_report.queue_class_report') as queue:
            pipeline.analyze_class('pt','class');queue.assert_not_called()
        self.assertIsNotNone(api.serialize_student_report(self.session))
        self.assertEqual(db.fetch_one("SELECT status FROM class_diagnosis_reports WHERE teaching_id='pt'")['status'],'failed')

    def test_detail_reuses_diagnosis_and_only_generates_feedback(self):
        original=self.generate(self.session)['report']['result']
        with patch('backend.app.services.ai._run_structured_skill',return_value={'feedback':'详细反馈'*160}),patch.object(api,'run_student_diagnosis_report') as diagnose,patch.object(api,'queue_class_report'):
            detailed=api.generate_student_report(self.session,BackgroundTasks())['report']
            diagnose.assert_not_called()
        self.assertEqual(detailed['result'],original);self.assertEqual(detailed['report_kind'],'detailed')
