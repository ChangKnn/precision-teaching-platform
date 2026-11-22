"""Optional activity feedback is persisted without becoming academic evidence."""
import json
from unittest import TestCase
from fastapi import BackgroundTasks
from backend import test_task_version_runtime as fixtures
from backend.app import student_api, database as db
from backend.app.schemas import StudentTaskSubmit


class StudentActivityFeedbackTest(TestCase):
    def setUp(self):
        self.case = fixtures.TaskVersionRuntimeTest('test_unchanged_publish_does_not_create_spurious_versions')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.session = self.case.start()['session']

    def test_feedback_persists_for_student_and_teacher_but_is_not_diagnosis_evidence(self):
        feedback = '这次学会了说明理由，希望 AI 少重复问题。'
        self.case.send(self.session['id'], '我认为需要结合条件说明理由。')
        student_api.submit_task(self.session['id'], StudentTaskSubmit(activity_feedback='  '+feedback+'  '), self.case.student)
        restored = student_api.get_student_session(self.session['id'], self.case.student)
        self.assertEqual(restored['activity_feedback'], feedback)
        result = student_api.student_results('pt', BackgroundTasks())['results'][0]
        self.assertEqual(result['activity_feedback'], feedback)
        payload, _ = student_api.build_student_diagnosis_input(self.session['id'])
        self.assertEqual(payload['submitted_text'], '')
        self.assertNotIn(feedback, json.dumps(payload, ensure_ascii=False))
        db.initialize_database()  # Existing databases retain feedback after repeated initialization.
        self.assertEqual(db.fetch_one('SELECT activity_feedback FROM student_task_sessions WHERE id=?', (self.session['id'],))['activity_feedback'], feedback)

    def test_empty_feedback_can_submit_without_text_or_photo(self):
        result = student_api.submit_task(self.session['id'], StudentTaskSubmit(activity_feedback='  '), self.case.student)
        self.assertEqual(result['status'], 'submitted')
        self.assertEqual(result['activity_feedback'], '')
        self.assertEqual(result['submitted_text'], '')
