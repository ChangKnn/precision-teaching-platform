import asyncio
import io
from unittest import TestCase
from unittest.mock import patch

from fastapi import BackgroundTasks, HTTPException, Request, Response
from PIL import Image

from backend import test_task_version_runtime as fixtures
from backend.app import student_api, main
from backend.test_teacher_auth import request as teacher_request
from backend.app.schemas import StudentTaskSubmit


class StudentPhotoSubmissionTest(TestCase):
    def setUp(self):
        self.case = fixtures.TaskVersionRuntimeTest('test_unchanged_publish_does_not_create_spurious_versions')
        self.case.setUp(); self.addCleanup(self.case.doCleanups)
        self.session = self.case.start()['session']
        output = io.BytesIO(); Image.new('RGB', (4, 4), 'red').save(output, 'JPEG'); self.photo = output.getvalue()

    def upload(self, body=None, content_type=b'image/jpeg', student=None):
        async def receive(): return {'type':'http.request', 'body':self.photo if body is None else body, 'more_body':False}
        request = Request({'type':'http', 'method':'POST', 'path':'/', 'headers':[(b'content-type', content_type)]}, receive)
        return asyncio.run(student_api.upload_file(self.session['id'], request, student or self.case.student, 'work.jpg'))

    def test_photo_is_visible_in_teacher_record_and_only_owner_can_access(self):
        photo = self.upload()
        self.assertTrue(student_api.student_submission_file(self.session['id'], photo['id'], self.case.student).path.is_file())
        with self.assertRaises(HTTPException):
            student_api.student_submission_file(self.session['id'], photo['id'], {**self.case.student, 'id':'other'})
        student_api.submit_task(self.session['id'], StudentTaskSubmit(submitted_text='完成', task_version_id=self.session['task_version_id']), self.case.student)
        with self.assertRaises(HTTPException):
            student_api.delete_student_submission_file(self.session['id'], photo['id'], self.case.student)
        result = student_api.student_results('pt', BackgroundTasks())['results'][0]
        self.assertEqual(result['submissions'][0]['id'], photo['id'])
        self.assertTrue(result['submissions'][0]['file_url'].endswith('/file'))
        self.assertTrue(student_api.submission_file(photo['id']).path.is_file())

    def test_delete_and_reject_bad_images(self):
        with self.assertRaises(HTTPException): self.upload(body=b'not an image')
        photo = self.upload()
        path = student_api.student_submission_file(self.session['id'], photo['id'], self.case.student).path
        student_api.delete_student_submission_file(self.session['id'], photo['id'], self.case.student)
        self.assertFalse(path.exists())
        self.assertEqual(student_api.serialize_session(student_api.owned_session(self.session['id'], 'stu'))['submissions'], [])

    def test_teacher_photo_endpoint_is_scoped_to_own_workspace(self):
        photo = self.upload()
        async def next_response(_): return Response(status_code=204)
        async def access(workspace):
            with patch.object(main, 'current_account', return_value={'role':'teacher', 'workspace_id':workspace, 'must_change_password':False}):
                return await main.protect_teacher_api(teacher_request(f"/api/teacher/submissions/{photo['id']}/file"), next_response)
        self.assertEqual(asyncio.run(access('ws')).status_code, 204)
        self.assertEqual(asyncio.run(access('other-workspace')).status_code, 404)
