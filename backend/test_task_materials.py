"""Image validation, teacher verification, canonical inputs and access boundaries."""
import asyncio
from dataclasses import replace
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from fastapi import HTTPException, Request
from PIL import Image

from backend.app import database as db, task_materials as materials
from backend.app import main, student_api
from backend.app.schemas import DiagnosisTaskUpdate
from backend.app.services.student_dialogue_skill import freeze_dialogue_config


class TaskMaterialsTest(TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.settings = replace(materials.settings, database_path=Path(folder.name) / 'test.db',
                                upload_path=Path(folder.name) / 'uploads', seed_demo_data=False,
                                task_vision_provider='mock', task_vision_model='', task_vision_api_key='')
        for module in (db, materials):
            target = patch.object(module, 'settings', self.settings)
            target.start()
            self.addCleanup(target.stop)
        db.initialize_database()
        now = db.utc_now()
        with db.database() as connection:
            connection.execute("""INSERT INTO teacher_workspaces
                (id, school_name, subject, teacher_name, identity_key, created_at, updated_at)
                VALUES ('ws', '测试学校', '数学', '教师', 'identity', ?, ?)""", (now, now))
            connection.execute("""INSERT INTO precision_teachings
                (id,workspace_id,title,goal,content,rationale,subject,grade,textbook,created_at,updated_at)
                VALUES ('pt','ws','测试数学任务','说明理由','角与向量','','数学','高一','空间向量',?,?)""", (now, now))
            connection.execute("""INSERT INTO diagnosis_tasks
                (teaching_id,goal,task_text,ai_role,updated_at) VALUES ('pt','说明理由',?, '中性引导者：只追问理由，不提供解法',?)""",
                (r'求 \(x^2+1\) 的最小值，请说明理由。', now))
            connection.execute("INSERT INTO classrooms (id,name,created_at,updated_at) VALUES ('class','测试班',?,?)", (now, now))
            connection.execute("UPDATE diagnosis_tasks SET design_intent='观察学生如何依据条件说明理由，不只看最终答案' WHERE teaching_id='pt'")
            connection.execute("INSERT INTO classroom_workspaces VALUES ('class','ws',?)", (now,))
            connection.execute("INSERT INTO diagnosis_assignments VALUES ('pt','class',?,1)", (now,))
        output = io.BytesIO()
        Image.new('RGB', (80, 60), 'white').save(output, 'PNG')
        self.png = output.getvalue()

    def upload(self, content=None):
        async def receive():
            return {'type': 'http.request', 'body': content if content is not None else self.png, 'more_body': False}
        request = Request({'type': 'http', 'method': 'POST', 'path': '/api/precision-teachings/pt/materials/images',
                           'headers': [(b'content-type', b'image/png')]}, receive=receive)
        return asyncio.run(materials.upload_task_image('pt', request))

    def confirm(self):
        state = materials.material_state('pt')
        return materials.confirm_materials('pt', materials.MaterialConfirmation(source_hash=state['source_hash']))

    def session(self):
        now = db.utc_now()
        with db.database() as connection:
            connection.execute("INSERT INTO students VALUES ('stu','class','测试学校','测试班','模拟学生',?,?)", (now, now))
            connection.execute("""INSERT INTO student_task_sessions
                (id,student_id,teaching_id,started_at,last_active_at) VALUES ('session','stu','pt',?,?)""", (now, now))

    def test_text_only_legacy_task_needs_no_new_confirmation(self):
        self.assertFalse(materials.material_state('pt')['confirmation_required'])
        materials.require_confirmed_materials('pt')

    def test_text_only_with_legacy_notes_does_not_require_image_review(self):
        state = materials.material_state('pt')
        materials.save_material_notes('pt', materials.MaterialNotes(source_hash=state['source_hash'], geometry_notes='此为旧版输入体验说明，并非图片识别结果。'))
        state = materials.material_state('pt')
        self.assertEqual(state['images'], [])
        self.assertFalse(state['confirmation_required'])
        materials.require_confirmed_materials('pt')
        self.assertIn('旧版输入体验说明', materials.canonical_task_text('pt'))

    def test_removing_last_image_keeps_notes_without_blocking(self):
        state = self.upload()
        state = materials.save_material_notes('pt', materials.MaterialNotes(source_hash=state['source_hash'], geometry_notes='教师补充的文字条件。'))
        state = materials.remove_task_image('pt', state['images'][0]['id'], materials.MaterialConfirmation(source_hash=state['source_hash']))
        self.assertFalse(state['confirmation_required'])
        self.assertIn('教师补充', materials.canonical_task_text('pt'))
        materials.require_confirmed_materials('pt')

    def test_empty_image_review_explains_the_actual_missing_content(self):
        state = self.upload()
        with self.assertRaises(HTTPException) as error:
            materials.review_recognition('pt', materials.ReadingReview(source_hash=state['source_hash']))
        self.assertEqual(error.exception.status_code, 422)
        self.assertIn('附件识别内容尚为空', error.exception.detail)
        self.assertNotIn('task_text', error.exception.detail)

    def test_review_without_images_is_not_a_missing_text_error(self):
        with self.assertRaises(HTTPException) as error:
            materials.review_recognition('pt', materials.ReadingReview(source_hash=materials.material_state('pt')['source_hash']))
        self.assertEqual(error.exception.status_code, 409)
        self.assertIn('没有图片附件', error.exception.detail)

    def test_image_blocks_rubric_until_verified(self):
        state = self.upload()
        self.assertEqual(len(state['images']), 1)
        self.assertFalse(state['confirmed'])
        with self.assertRaises(HTTPException) as error:
            main.generate_diagnosis_rubric('pt')
        self.assertEqual(error.exception.status_code, 409)
        self.assertTrue(self.confirm()['confirmed'])
        materials.require_confirmed_materials('pt')

    def test_teacher_notes_reach_rubric_and_frozen_student_task(self):
        state = self.upload()
        materials.save_material_notes('pt', materials.MaterialNotes(source_hash=state['source_hash'], geometry_notes=r'已知 \(AB\perp CD\)。'))
        self.confirm()
        canonical = materials.canonical_task_text('pt')
        self.assertIn(r'AB\perp CD', canonical)
        self.assertEqual(main.build_rubric_input('pt')['diagnostic_task']['task_text'], canonical)
        with db.database() as connection:
            connection.execute("UPDATE diagnosis_tasks SET status='published' WHERE teaching_id='pt'")
        self.assertEqual(freeze_dialogue_config('pt')['diagnostic_task']['task_content'], canonical)

    def test_stale_confirmation_and_notes_write_are_rejected(self):
        state = self.upload()
        self.upload()
        with self.assertRaises(HTTPException):
            materials.confirm_materials('pt', materials.MaterialConfirmation(source_hash=state['source_hash']))
        with self.assertRaises(HTTPException):
            materials.save_material_notes('pt', materials.MaterialNotes(source_hash=state['source_hash'], geometry_notes='旧画面'))

    def test_new_image_invalidates_rubric_and_confirmation(self):
        self.upload()
        self.confirm()
        with db.database() as connection:
            now = db.utc_now()
            connection.execute("""INSERT INTO diagnosis_rubrics
                (teaching_id,skill_key,skill_version,provider,status,input_hash,generated_json,current_json,generated_at,updated_at)
                VALUES ('pt','solo_task_rubric_builder','1','mock','confirmed','h','{}','{}',?,?)""", (now, now))
        state = self.upload()
        self.assertFalse(state['confirmed'])
        self.assertEqual(db.fetch_one("SELECT status FROM diagnosis_rubrics WHERE teaching_id='pt'")['status'], 'stale')

    def test_text_edit_invalidates_confirmation(self):
        self.upload()
        self.confirm()
        main.update_diagnosis('pt', DiagnosisTaskUpdate(goal='说明理由', task_text='改为求二次式最大值'))
        self.assertFalse(materials.material_state('pt')['confirmed'])

    def test_started_session_locks_task_materials_and_wording(self):
        state = self.upload()
        self.confirm()
        self.session()
        for action in (
            lambda: self.upload(),
            lambda: materials.remove_task_image('pt', state['images'][0]['id'], materials.MaterialConfirmation(source_hash=state['source_hash'])),
            lambda: materials.save_material_notes('pt', materials.MaterialNotes(source_hash=state['source_hash'], geometry_notes='换题')),
            lambda: main.update_diagnosis('pt', DiagnosisTaskUpdate(goal='说明理由', task_text='新题目')),
        ):
            with self.subTest(action=action), self.assertRaises(HTTPException) as error:
                action()
            self.assertEqual(error.exception.status_code, 409)

    def test_invalid_image_and_oversized_payload_rejected(self):
        for content, code in ((b'<svg onload="alert(1)"/>', 415), (b'x' * (materials.MAX_IMAGE_BYTES + 1), 413)):
            with self.subTest(code=code), self.assertRaises(HTTPException) as error:
                self.upload(content)
            self.assertEqual(error.exception.status_code, code)
        self.assertEqual(materials.image_rows('pt'), [])

    def test_max_four_images(self):
        for _ in range(4):
            self.upload()
        with self.assertRaises(HTTPException) as error:
            self.upload()
        self.assertEqual(error.exception.status_code, 422)

    def test_mock_never_fakes_recognition_or_makes_network_call(self):
        self.upload()
        with patch.object(materials, 'urlopen') as network:
            with self.assertRaises(HTTPException) as error:
                materials.recognize_task_images('pt')
            self.assertEqual(error.exception.status_code, 409)
            network.assert_not_called()

    def test_vision_request_carries_image_and_returns_only_draft(self):
        self.upload()
        response = {'choices': [{'message': {'content': json.dumps({
            'task_text': r'计算 \(x^2\)', 'geometry_notes': '', 'uncertainties': ['下标请核对'],
        }, ensure_ascii=False)}}]}
        settings = replace(self.settings, task_vision_provider='openrouter', task_vision_model='vision-test', task_vision_api_key='test-only')
        with patch.object(materials, 'settings', settings), patch.object(materials, 'urlopen', return_value=io.BytesIO(json.dumps(response).encode())) as network:
            result = materials.recognize_task_images('pt')
            request = network.call_args.args[0]
            body = json.loads(request.data)
            self.assertTrue(body['messages'][1]['content'][1]['image_url']['url'].startswith('data:image/png;base64,'))
        self.assertEqual(result['result']['uncertainties'], ['下标请核对'])
        self.assertFalse(materials.material_state('pt')['confirmed'])
        self.assertNotEqual(db.fetch_one("SELECT task_text FROM diagnosis_tasks WHERE teaching_id='pt'")['task_text'], result['result']['task_text'])

    def test_student_image_requires_assigned_class_and_school(self):
        state = self.upload()
        image_id = state['images'][0]['id']
        student = {'classroom_id': 'class', 'school_name': '测试学校'}
        with self.assertRaises(HTTPException):
            student_api.student_task_image('pt', image_id, student)
        with db.database() as connection:
            connection.execute("UPDATE diagnosis_tasks SET status='published' WHERE teaching_id='pt'")
        self.assertTrue(Path(student_api.student_task_image('pt', image_id, student).path).is_file())
        for outsider in ({'classroom_id': 'other', 'school_name': '测试学校'}, {'classroom_id': 'class', 'school_name': '其他学校'}):
            with self.assertRaises(HTTPException):
                student_api.student_task_image('pt', image_id, outsider)

    def test_teacher_other_workspace_cannot_access_image(self):
        state = self.upload()
        request = Request({'type': 'http', 'method': 'GET', 'path': f"/api/precision-teachings/pt/materials/images/{state['images'][0]['id']}", 'headers': []})
        async def next_request(_):
            raise AssertionError('cross-workspace request must not reach image handler')
        other_teacher = {'account_id': 'other-teacher', 'role': 'teacher',
                         'workspace_id': 'other', 'must_change_password': False}
        with patch.object(main, 'current_account', return_value=other_teacher):
            response = asyncio.run(main.protect_teacher_api(request, next_request))
        self.assertEqual(response.status_code, 404)
