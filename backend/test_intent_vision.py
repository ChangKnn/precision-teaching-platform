"""Versioned attachment reading, teacher-only intent and shared task contract."""
import asyncio
import json
from dataclasses import replace
from unittest import TestCase
from unittest.mock import patch

import jsonschema
from fastapi import BackgroundTasks, HTTPException, Request

from backend import test_task_materials as legacy_tests
from backend.app import database as db, main, task_materials as m
from backend.app.schemas import DiagnosisTaskUpdate
from backend.app.services.skills import load_skill
from backend.app.services.student_dialogue_skill import freeze_dialogue_config


class IntentVisionTest(TestCase):
    setUp = legacy_tests.TaskMaterialsTest.setUp
    upload = legacy_tests.TaskMaterialsTest.upload
    confirm = legacy_tests.TaskMaterialsTest.confirm
    session = legacy_tests.TaskMaterialsTest.session

    def read(self, uncertainties=None):
        self.upload()
        tasks = BackgroundTasks()
        configured = replace(self.settings, task_vision_provider='openrouter', task_vision_model='test-vision', task_vision_api_key='test')
        with patch.object(m, 'settings', configured):
            queued = m.queue_recognition('pt', tasks)
            self.assertEqual(queued['recognition']['status'], 'processing')
            with patch.object(m, 'recognize_task_images', return_value={'result': {
                'task_text': r'附件1：已知 \(AB=2\)，解释判断依据。',
                'geometry_notes': '图中明确标注 AB 垂直 CD；横轴为时间（秒）。',
                'uncertainties': uncertainties or [],
            }}):
                asyncio.run(tasks())
        return m.material_state('pt')

    def test_intent_persists_and_is_not_omitted_by_legacy_clients(self):
        original = db.fetch_one("SELECT * FROM diagnosis_tasks WHERE teaching_id='pt'")
        main.update_diagnosis('pt', DiagnosisTaskUpdate(goal=original['goal'], task_text=original['task_text'], design_intent='观察策略选择，不只看计算答案'))
        main.update_diagnosis('pt', DiagnosisTaskUpdate(goal=original['goal'], task_text=original['task_text']))
        payload = main.build_rubric_input('pt')
        self.assertIn('策略选择', payload['diagnostic_task_design_intent'])
        schema = json.loads((load_skill('solo_task_rubric_builder').path.parent / 'assets/task-rubric-input.schema.json').read_text(encoding='utf-8'))
        jsonschema.validate(payload, schema)

    def test_intent_changes_invalidate_existing_rubric(self):
        now = db.utc_now()
        with db.database() as connection:
            connection.execute("""INSERT INTO diagnosis_rubrics
                (teaching_id,skill_key,skill_version,provider,status,input_hash,generated_json,current_json,generated_at,updated_at)
                VALUES ('pt','solo_task_rubric_builder','1','mock','confirmed','h','{}','{}',?,?)""", (now,now))
        original = db.fetch_one("SELECT * FROM diagnosis_tasks WHERE teaching_id='pt'")
        main.update_diagnosis('pt', DiagnosisTaskUpdate(goal=original['goal'],task_text=original['task_text'],design_intent='新的观察重点'))
        self.assertEqual(db.fetch_one("SELECT status FROM diagnosis_rubrics WHERE teaching_id='pt'")['status'],'stale')

    def test_automatic_clear_reading_is_saved_and_shared(self):
        state = self.read()
        self.assertTrue(state['confirmed'])
        self.assertEqual(state['recognition']['status'], 'ready')
        m.require_confirmed_materials('pt')
        payload = main.build_rubric_input('pt')
        self.assertIn('AB=2', payload['diagnostic_task']['task_text'])
        self.assertIn('横轴为时间', payload['diagnostic_task']['task_text'])
        with db.database() as connection:
            connection.execute("UPDATE diagnosis_tasks SET status='published', design_intent='私有困难假设' WHERE teaching_id='pt'")
        frozen = freeze_dialogue_config('pt')
        self.assertEqual(frozen['diagnostic_task']['task_content'], payload['diagnostic_task']['task_text'])
        self.assertNotIn('私有困难假设', json.dumps(frozen,ensure_ascii=False))

    def test_uncertain_reading_blocks_analysis_until_reviewed(self):
        state = self.read(['AB 的长度看不清'])
        self.assertEqual(state['recognition']['status'], 'review')
        with self.assertRaises(HTTPException): m.require_confirmed_materials('pt')
        with self.assertRaises(HTTPException): self.confirm()
        self.assertNotIn('AB=2', m.canonical_task_text('pt'))
        saved = m.review_recognition('pt',m.ReadingReview(source_hash=state['source_hash'],task_text='附件1：AB=3，说明依据',diagram_notes='图上标注垂直关系',checked=True))
        self.assertTrue(saved['recognition']['reviewed'])
        self.assertEqual(saved['recognition']['uncertainties'],[])
        self.assertIn('AB=3',m.canonical_task_text('pt'))
        self.assertNotIn('AB=2',m.canonical_task_text('pt'))

    def test_mock_is_explicit_and_manual_reading_can_unlock(self):
        self.upload()
        with patch.object(m,'urlopen') as network:
            state=m.queue_recognition('pt',BackgroundTasks())
            self.assertEqual(state['recognition']['status'],'disabled')
            network.assert_not_called()
        state=m.review_recognition('pt',m.ReadingReview(source_hash=state['source_hash'],task_text='附件1：请评价史料的来源和立场',checked=True))
        m.require_confirmed_materials('pt')
        self.assertIn('史料',m.canonical_task_text('pt'))

    def test_failure_preserves_original_and_is_retryable(self):
        self.upload();tasks=BackgroundTasks()
        configured=replace(self.settings,task_vision_provider='openai',task_vision_model='test',task_vision_api_key='secret')
        with patch.object(m,'settings',configured),patch.object(m,'recognize_task_images',side_effect=RuntimeError('secret provider failure')):
            m.queue_recognition('pt',tasks);asyncio.run(tasks())
        state=m.material_state('pt')
        self.assertEqual(state['recognition']['status'],'failed')
        self.assertNotIn('secret',state['recognition']['error'])
        self.assertEqual(len(state['images']),1)

    def test_replaced_images_reject_old_review_and_old_worker(self):
        state=self.read(['模糊']);self.upload()
        with self.assertRaises(HTTPException):
            m.review_recognition('pt',m.ReadingReview(source_hash=state['source_hash'],task_text='旧图内容',checked=True))
        self.assertNotIn('AB=2',m.canonical_task_text('pt'))
        self.assertFalse(m.material_state('pt')['confirmed'])

    def test_worker_cannot_overwrite_manual_review(self):
        self.upload();tasks=BackgroundTasks()
        configured=replace(self.settings,task_vision_provider='openai',task_vision_model='test',task_vision_api_key='test')
        with patch.object(m,'settings',configured):
            state=m.queue_recognition('pt',tasks)
            token=db.fetch_one("SELECT job_token FROM diagnosis_image_readings WHERE teaching_id='pt'")['job_token']
            m.review_recognition('pt',m.ReadingReview(source_hash=state['source_hash'],task_text='人工核对结果',checked=True))
            with patch.object(m,'recognize_task_images') as network:
                m.process_recognition('pt',token);network.assert_not_called()
        self.assertIn('人工核对结果',m.canonical_task_text('pt'))

    def test_session_lock_covers_intent_reading_and_recognition(self):
        state=self.read();self.session()
        original=db.fetch_one("SELECT * FROM diagnosis_tasks WHERE teaching_id='pt'")
        for action in (
            lambda:m.queue_recognition('pt',BackgroundTasks()),
            lambda:m.review_recognition('pt',m.ReadingReview(source_hash=state['source_hash'],task_text='替换',checked=True)),
            lambda:main.update_diagnosis('pt',DiagnosisTaskUpdate(goal=original['goal'],task_text=original['task_text'],design_intent='替换意图')),
        ):
            with self.subTest(action=action),self.assertRaises(HTTPException):action()

    def test_unresolved_marker_or_unchecked_review_is_rejected(self):
        self.upload();state=m.material_state('pt')
        for text,checked in (('识别结果',False),('[待核对]',True),(' ',True)):
            with self.subTest(text=text),self.assertRaises(HTTPException):
                m.review_recognition('pt',m.ReadingReview(source_hash=state['source_hash'],task_text=text,checked=checked))

    def test_processing_timeout_can_be_retried(self):
        self.upload()
        with db.database() as c:
            c.execute("INSERT INTO diagnosis_image_readings (teaching_id,image_hash,status,updated_at) VALUES (?,?,'processing','2000-01-01T00:00:00+00:00')",('pt',m.image_version('pt')))
        self.assertEqual(m.material_state('pt')['recognition']['status'],'failed')

    def test_repeated_start_does_not_duplicate_processing_job(self):
        self.upload();tasks=BackgroundTasks()
        configured=replace(self.settings,task_vision_provider='openai',task_vision_model='test',task_vision_api_key='test')
        with patch.object(m,'settings',configured):
            m.queue_recognition('pt',tasks);m.queue_recognition('pt',tasks,force=True)
            self.assertEqual(len(tasks.tasks),1)

    def test_new_intent_during_rubric_generation_rejects_old_result(self):
        from backend.app.services.ai import _mock_task_rubric
        def concurrent_result(skill, payload, provider):
            with db.database() as c:
                c.execute("UPDATE diagnosis_tasks SET design_intent='生成期间改变' WHERE teaching_id='pt'")
            return _mock_task_rubric(payload)
        with patch.object(main,'run_skill',side_effect=concurrent_result), self.assertRaises(HTTPException) as error:
            main.generate_diagnosis_rubric('pt')
        self.assertEqual(error.exception.status_code,409)
        self.assertIsNone(db.fetch_one("SELECT * FROM diagnosis_rubrics WHERE teaching_id='pt'"))

    def test_upload_schedules_background_reading_and_batch_can_defer(self):
        configured=replace(self.settings,task_vision_provider='openai',task_vision_model='test',task_vision_api_key='test')
        def upload_with_tasks(defer):
            async def receive():
                return {'type':'http.request','body':self.png,'more_body':False}
            headers=[(b'content-type',b'image/png')]
            if defer:headers.append((b'x-defer-recognition',b'1'))
            request=Request({'type':'http','method':'POST','path':'/api/precision-teachings/pt/materials/images','headers':headers},receive=receive)
            tasks=BackgroundTasks()
            state=asyncio.run(m.upload_task_image('pt',request,tasks))
            return state,tasks
        with patch.object(m,'settings',configured):
            state,tasks=upload_with_tasks(False)
            self.assertEqual(state['recognition']['status'],'processing')
            self.assertEqual(len(tasks.tasks),1)

            with patch.object(m,'recognize_task_images',return_value={'result':{'task_text':'附件文字','geometry_notes':'','uncertainties':[]}}):
                asyncio.run(tasks())
            self.assertEqual(m.material_state('pt')['recognition']['status'],'ready')
            state,tasks=upload_with_tasks(True)
            self.assertEqual(len(tasks.tasks),0)
            self.assertEqual(state['recognition']['status'],'pending')
            m.queue_recognition('pt',tasks)
            self.assertEqual(len(tasks.tasks),1)

    def test_save_is_review_without_separate_checkbox(self):
        state=self.read(['图中关系请核对'])
        merged=state['recognition']['task_text']+'\n\n'+state['recognition']['diagram_notes']
        saved=m.review_recognition('pt',m.ReadingReview(source_hash=state['source_hash'],task_text=merged))
        self.assertEqual(saved['recognition']['status'],'ready')
        self.assertEqual(saved['recognition']['diagram_notes'],'')
        self.assertIn('横轴为时间',m.canonical_task_text('pt'))
