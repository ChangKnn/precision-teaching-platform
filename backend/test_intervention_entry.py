"""Entering intervention never depends on individual review or delivery."""
from unittest import TestCase
from fastapi import HTTPException
from backend import test_task_version_runtime as runtime
from backend.app import main, student_api, database as db
from backend.app.schemas import StudentTaskSubmit, InterventionPlanUpdate

class InterventionEntryTest(TestCase):
    setUp = runtime.TaskVersionRuntimeTest.setUp
    confirm_rubric = runtime.TaskVersionRuntimeTest.confirm_rubric
    publish = runtime.TaskVersionRuntimeTest.publish
    save = runtime.TaskVersionRuntimeTest.save
    start = runtime.TaskVersionRuntimeTest.start

    def submit(self):
        session=self.start()['session']['id']
        student_api.submit_task(session,StudentTaskSubmit(submitted_text='模拟学生的个人判断与依据'),self.student)

    def test_zero_reviewed_and_zero_pushed_reports_can_enter_and_save(self):
        self.submit()
        before=db.fetch_all("SELECT * FROM student_diagnosis_reports")
        current=main.workspace_for('pt')
        self.assertNotEqual(current['feedback']['status'],'confirmed')
        self.assertEqual(current['feedback']['confirmed_count'],0)
        self.assertTrue(current['stages']['intervention']['accessible'])
        self.assertNotEqual(main.ordered_teachings()[0]['intervention_status'],'locked')
        saved=main.update_intervention('pt',InterventionPlanUpdate(teacher_judgment='教师准备先设计课堂支持'))
        self.assertEqual(saved['intervention']['teacher_judgment'],'教师准备先设计课堂支持')
        self.assertNotEqual(saved['feedback']['status'],'confirmed')
        self.assertEqual(db.fetch_all("SELECT * FROM student_diagnosis_reports"),before)

    def test_no_submission_still_waits_for_evidence(self):
        self.assertFalse(main.workspace_for('pt')['stages']['intervention']['accessible'])
        with self.assertRaises(HTTPException):
            main.update_intervention('pt',InterventionPlanUpdate())

    def test_draft_task_does_not_open_intervention(self):
        self.submit()
        self.save('draft')
        self.assertFalse(main.workspace_for('pt')['stages']['intervention']['accessible'])
