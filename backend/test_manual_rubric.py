from unittest import TestCase
from unittest.mock import patch

from fastapi import HTTPException

from backend import test_task_materials as base
from backend.app import database as db, main
from backend.app.schemas import DiagnosisRubricUpdate
from backend.app.services.manual_rubric import FIELDS, LEVELS


class ManualRubricTest(TestCase):
    setUp = base.TaskMaterialsTest.setUp

    def payload(self, complete=False, confirmed=False):
        return DiagnosisRubricUpdate(rubric={'levels':{level:{field:[f'{level}教师填写的{field}'] if complete else [] for field in FIELDS} for level in LEVELS}}, confirmed=confirmed)

    def test_first_partial_draft_needs_no_ai(self):
        payload=self.payload()
        payload.rubric['levels']['P']['core_performance']=['只描述无关信息']
        with patch('backend.app.main.run_skill', side_effect=AssertionError('Must not call AI')):
            result=main.update_diagnosis_rubric('pt',payload)['rubric']
        self.assertEqual(result['provider'],'teacher')
        self.assertEqual(result['status'],'draft')
        self.assertEqual(result['rubric']['levels']['P']['core_performance'],['只描述无关信息'])
        self.assertEqual(result['rubric']['task_analysis']['relations']['items'],[])
        self.assertEqual(db.fetch_one('SELECT COUNT(*) AS n FROM ai_jobs')['n'],0)

    def test_empty_or_incomplete_cannot_confirm(self):
        with self.assertRaises(HTTPException) as caught:
            main.update_diagnosis_rubric('pt',self.payload(confirmed=True))
        self.assertEqual(caught.exception.status_code,422)
        self.assertIn('主要认知特征和学生表现',str(caught.exception.detail))
        self.assertIsNone(main.serialized_rubric('pt'))

    def test_teacher_rubric_requires_examples_for_confirmation_but_can_save_draft(self):
        payload=self.payload(complete=True,confirmed=True)
        for row in payload.rubric['levels'].values():row['typical_expressions']=[]
        with self.assertRaises(HTTPException) as caught:
            main.update_diagnosis_rubric('pt',payload)
        self.assertEqual(caught.exception.status_code,422)
        self.assertIn('示例',caught.exception.detail)
        payload.confirmed=False
        self.assertEqual(main.update_diagnosis_rubric('pt',payload)['rubric']['status'],'draft')

    def test_complete_teacher_rubric_can_confirm_with_all_three_fields(self):
        result=main.update_diagnosis_rubric('pt',self.payload(complete=True,confirmed=True))['rubric']
        self.assertEqual(result['status'],'confirmed')
        self.assertTrue(result['confirmed_at'])
        self.assertEqual(result['provider'],'teacher')

    def test_manual_edit_does_not_require_regeneration(self):
        main.update_diagnosis_rubric('pt',self.payload(complete=True,confirmed=True))
        payload=self.payload(complete=True)
        payload.rubric['levels']['R']['core_performance']=['教师修改后的关系整合标准']
        result=main.update_diagnosis_rubric('pt',payload)['rubric']
        self.assertEqual(result['status'],'draft')
        self.assertIsNone(result['confirmed_at'])
        self.assertEqual(result['rubric']['levels']['R']['core_performance'],['教师修改后的关系整合标准'])

    def test_unknown_teaching_and_malformed_levels_rejected(self):
        with self.assertRaises(HTTPException):main.update_diagnosis_rubric('missing',self.payload())
        payload=self.payload();payload.rubric['levels']['X']={}
        with self.assertRaises(HTTPException) as caught:main.update_diagnosis_rubric('pt',payload)
        self.assertEqual(caught.exception.status_code,422)

    def test_image_review_still_required_for_confirmation(self):
        base.TaskMaterialsTest.upload(self)
        main.update_diagnosis_rubric('pt',self.payload(complete=True))
        with self.assertRaises(HTTPException) as caught:
            main.update_diagnosis_rubric('pt',self.payload(complete=True,confirmed=True))
        self.assertEqual(caught.exception.status_code,409)

    def test_legacy_rubric_reconfirmation_preserves_fields_and_archives_evidence(self):
        main.update_diagnosis_rubric('pt', self.payload(complete=True, confirmed=True))
        base.TaskMaterialsTest.session(self)
        with db.database() as connection:
            connection.execute("UPDATE diagnosis_tasks SET design_intent = '' WHERE teaching_id = 'pt'")
        before = db.fetch_one("SELECT * FROM diagnosis_tasks WHERE teaching_id = 'pt'")
        existing = main.serialized_rubric('pt')['rubric']
        with patch('backend.app.main.run_skill', side_effect=AssertionError('Must not call AI')):
            result = main.update_diagnosis_rubric('pt', DiagnosisRubricUpdate(rubric=existing, confirmed=True))
        self.assertEqual(result['rubric']['status'], 'confirmed')
        after=db.fetch_one("SELECT * FROM diagnosis_tasks WHERE teaching_id = 'pt'")
        self.assertEqual({k:v for k,v in after.items() if k!='published_version_id'},
                         {k:v for k,v in before.items() if k!='published_version_id'})
        self.assertIsNotNone(after['published_version_id'])
        from backend.app.services.task_versions import get_version
        self.assertEqual(get_version(after['published_version_id'])['snapshot']['rubric'],existing)

    def test_new_task_without_intent_cannot_confirm_first_manual_rubric(self):
        with db.database() as connection:
            connection.execute("UPDATE diagnosis_tasks SET design_intent = '' WHERE teaching_id = 'pt'")
        with self.assertRaises(HTTPException) as caught:
            main.update_diagnosis_rubric('pt', self.payload(complete=True, confirmed=True))
        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn('诊断任务设计意图', caught.exception.detail)
        self.assertIsNone(main.serialized_rubric('pt'))

    def test_existing_rubric_without_sessions_still_requires_intent(self):
        main.update_diagnosis_rubric('pt', self.payload(complete=True))
        with db.database() as connection:
            connection.execute("UPDATE diagnosis_tasks SET design_intent = '' WHERE teaching_id = 'pt'")
        with self.assertRaises(HTTPException) as caught:
            main.update_diagnosis_rubric('pt', self.payload(complete=True, confirmed=True))
        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn('诊断任务设计意图', caught.exception.detail)
        self.assertEqual(main.serialized_rubric('pt')['status'], 'draft')

    def test_legacy_locked_task_still_cannot_generate_without_intent(self):
        main.update_diagnosis_rubric('pt', self.payload(complete=True, confirmed=True))
        base.TaskMaterialsTest.session(self)
        with db.database() as connection:
            connection.execute("UPDATE diagnosis_tasks SET design_intent = '' WHERE teaching_id = 'pt'")
        with patch('backend.app.main.run_skill', side_effect=AssertionError('Must not call AI')):
            with self.assertRaises(HTTPException) as caught:
                main.generate_diagnosis_rubric('pt')
        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn('诊断任务设计意图', caught.exception.detail)
        self.assertEqual(db.fetch_one('SELECT COUNT(*) AS n FROM ai_jobs')['n'], 0)
