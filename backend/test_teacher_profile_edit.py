"""Teacher profile changes retain class ownership and student task access."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from fastapi import HTTPException

from backend import test_task_version_runtime as fixtures
from backend.app import admin_api, database as db, main, student_api
from backend.app.schemas import TeacherProfileUpdate
from backend.app.teacher_auth import provision_teacher
from backend.app import task_materials as materials
from backend.app.services import task_versions as versions


class TeacherProfileEditTest(TestCase):
    def setUp(self):
        self.case = fixtures.TaskVersionRuntimeTest('test_unchanged_publish_does_not_create_spurious_versions')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.request = SimpleNamespace(state=SimpleNamespace(teacher_workspace_id='ws'))

    def test_teacher_school_change_preserves_existing_student_session_and_class(self):
        started = self.case.start()
        saved = main.update_teacher_profile(TeacherProfileUpdate(school_name='新学校', display_name='测试老师', subject='历史', years_experience=8), self.request)
        self.assertEqual(saved['school_name'], '新学校')
        self.assertEqual(db.fetch_one("SELECT school_name FROM classroom_schools WHERE classroom_id='class'")['school_name'], '测试学校')
        self.assertEqual(student_api.login_options()[0]['name'], '测试学校')
        self.assertIsNotNone(student_api.match_classroom('测试学校', '测试班'))
        self.assertEqual(len(student_api.task_rows(self.case.student)), 1)
        self.assertEqual(student_api.owned_session(started['session']['id'], 'stu')['id'], started['session']['id'])
        self.assertEqual(student_api.start_task('pt', self.case.student)['session']['id'], started['session']['id'])
        with self.assertRaises(HTTPException):
            student_api.start_task('pt', {**self.case.student, 'school_name':'新学校'})

    def test_admin_can_edit_profile_without_touching_teaching_style_or_login(self):
        account = provision_teacher('teacher-test', 'safe-Password-123', '', '', '', workspace_id='ws', must_change_password=False)
        before = db.fetch_one('SELECT * FROM teacher_workspaces WHERE id=?', ('ws',))
        with patch.object(admin_api, 'require_admin', return_value={'account_id':'admin'}):
            result = admin_api.update_teacher_identity(account['id'], admin_api.TeacherIdentityUpdate(school_name='新学校', subject='语文', teacher_name='新姓名'), self.request)
        after = db.fetch_one('SELECT * FROM teacher_workspaces WHERE id=?', ('ws',))
        self.assertEqual((result['school_name'], result['subject'], result['teacher_name']), ('新学校', '语文', '新姓名'))
        self.assertEqual(after['teaching_style'], before['teaching_style'])
        self.assertEqual(db.fetch_one('SELECT username FROM user_accounts WHERE id=?', (account['id'],))['username'], 'teacher-test')
        self.assertEqual(len(student_api.task_rows(self.case.student)), 1)

    def test_school_change_keeps_published_image_available_to_class_only(self):
        state = fixtures.base.TaskMaterialsTest.upload(self.case)
        materials.review_recognition('pt', materials.ReadingReview(source_hash=state['source_hash'], task_text='图片内容'))
        self.case.confirm_rubric(); self.case.publish()
        version = versions.published_version('pt')
        image = version['snapshot']['images'][0]
        main.update_teacher_profile(TeacherProfileUpdate(school_name='新学校', display_name='教师', subject='数学', years_experience=0), self.request)
        self.assertTrue(student_api.student_version_image('pt', version['id'], image['id'], self.case.student).path.is_file())
        with self.assertRaises(HTTPException):
            student_api.student_version_image('pt', version['id'], image['id'], {**self.case.student, 'school_name':'新学校'})

    def test_blank_and_duplicate_identity_are_rejected(self):
        with self.assertRaises(HTTPException) as blank:
            main.update_teacher_profile(TeacherProfileUpdate(school_name='  ', display_name='老师', subject='数学', years_experience=0), self.request)
        self.assertEqual(blank.exception.status_code, 422)
        provision_teacher('another-teacher', 'safe-Password-123', '新学校', '数学', '另一位老师', must_change_password=False)
        with self.assertRaises(HTTPException) as duplicate:
            main.update_teacher_profile(TeacherProfileUpdate(school_name='新学校', display_name='另一位老师', subject='数学', years_experience=0), self.request)
        self.assertEqual(duplicate.exception.status_code, 409)
