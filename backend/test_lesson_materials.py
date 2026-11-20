"""Lesson uploads remain private, reviewed, versioned and separate from diagnosis."""
import asyncio
import io
import json
import zipfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import quote
from docx import Document
from fastapi import Request, HTTPException
from backend import test_task_materials as fixture
from backend.app import lesson_materials as api, database as db
from backend.app.services.skills import load_skill
import jsonschema


class LessonMaterialsTest(TestCase):
    def setUp(self):
        fixture.TaskMaterialsTest.setUp(self)
        change=patch.object(api,'settings',self.settings);change.start();self.addCleanup(change.stop)

    def document(self):
        buffer=io.BytesIO();doc=Document();doc.add_paragraph('本课研究唐朝的繁荣，保留史料比较活动。');doc.save(buffer);return buffer.getvalue()

    def upload(self, content=None, revision=None, name='教案.docx'):
        async def receive():
            return {'type':'http.request','body':content or self.document(),'more_body':False}
        request=Request({'type':'http','method':'POST','path':'/api/precision-teachings/pt/lesson-materials',
            'headers':[(b'x-file-name',quote(name).encode()),(b'x-material-version',(revision or api.material_state('pt')['revision']).encode())]},receive)
        return asyncio.run(api.upload_material('pt',request))

    def test_upload_review_reopen_and_teacher_only_download(self):
        state=self.upload()
        with self.assertRaises(HTTPException):api.confirmed_materials('pt')
        reviewed=api.confirm_materials('pt',api.Review(revision=state['revision'],text='唐朝繁荣；教师先展示图片，再比较史料。'))
        self.assertTrue(reviewed['confirmed'])
        payload={};api.attach_materials(payload,'pt')
        self.assertIn('唐朝繁荣',payload['current_lesson_materials']['text'])
        self.assertEqual(api.get_materials('pt'),reviewed)
        self.assertEqual(api.download_material('pt',state['documents'][0]['id']).filename,'教案.docx')
        self.assertEqual(db.fetch_one("SELECT task_text FROM diagnosis_tasks WHERE teaching_id='pt'")['task_text'],r'求 \(x^2+1\) 的最小值，请说明理由。')
        self.assertEqual(db.fetch_all('SELECT * FROM diagnosis_role_knowledge'),[])
        with self.assertRaises(HTTPException):api.download_material('other',state['documents'][0]['id'])

    def test_stale_revision_and_modified_materials_do_not_silently_change_source(self):
        before=api.material_state('pt')['revision'];state=self.upload()
        with self.assertRaises(HTTPException):api.confirm_materials('pt',api.Review(revision=before,text='过期修改'))
        api.confirm_materials('pt',api.Review(revision=state['revision'],text='教师已核对'))
        payload={};api.attach_materials(payload,'pt')
        latest=api.material_state('pt')
        api.confirm_materials('pt',api.Review(revision=latest['revision'],text='教师更新教学主线'))
        with self.assertRaises(HTTPException):api.require_same_materials(payload,'pt')
        with self.assertRaises(HTTPException):self.upload()

    def test_removing_material_invalidates_review_but_not_diagnosis(self):
        state=self.upload();api.confirm_materials('pt',api.Review(revision=state['revision'],text='本课内容'))
        state=api.material_state('pt');api.remove_material('pt',state['documents'][0]['id'],api.Revision(revision=state['revision']))
        self.assertIsNone(api.confirmed_materials('pt'))
        self.assertIsNotNone(db.fetch_one("SELECT id FROM precision_teachings WHERE id='pt'"))

    def test_pptx_respects_presentation_order_and_marks_image_only_slides(self):
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:
            z.writestr('ppt/presentation.xml','<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId id="1" r:id="b"/><p:sldId id="2" r:id="a"/></p:sldIdLst></p:presentation>')
            z.writestr('ppt/_rels/presentation.xml.rels','<Relationships><Relationship Id="a" Target="slides/slide1.xml"/><Relationship Id="b" Target="slides/slide2.xml"/></Relationships>')
            z.writestr('ppt/slides/slide1.xml','<slide/>')
            z.writestr('ppt/slides/slide2.xml','<slide xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:p><a:r><a:t>先比较史料</a:t></a:r></a:p></slide>')
        text=api.extract_lesson_text('课件.pptx',out.getvalue())
        self.assertLess(text.index('先比较史料'),text.index('本页未提取到文字'))
        with self.assertRaises(HTTPException):api.extract_lesson_text('无效.pptx',b'bad zip')

    def test_all_downstream_input_contracts_accept_reviewed_materials(self):
        state=self.upload();api.confirm_materials('pt',api.Review(revision=state['revision'],text='教师教学内容'))
        for key,filename in [('precision_intervention_goal_path_design','goal-path'),('precision_intervention_activity_formative_regulation','activity-formative'),('precision_intervention_plan_integration_review','plan-integration')]:
            skill=load_skill(key)
            payload=json.loads((skill.path.parent/'tests/sample-input.json').read_text())
            api.attach_materials(payload,'pt')
            jsonschema.validate(payload,json.loads((skill.path.parent/f'assets/{filename}-input.schema.json').read_text()))

    def test_word_keeps_table_between_surrounding_paragraphs(self):
        doc=Document();doc.add_paragraph('先观察');doc.add_table(rows=1,cols=1).cell(0,0).text='再比较';doc.add_paragraph('最后解释')
        out=io.BytesIO();doc.save(out)
        text=api.extract_lesson_text('顺序.docx',out.getvalue())
        self.assertLess(text.index('先观察'),text.index('再比较'))
        self.assertLess(text.index('再比较'),text.index('最后解释'))

    def test_lesson_routes_require_owner_login(self):
        from backend.app import main
        from fastapi.responses import JSONResponse
        from unittest.mock import AsyncMock
        request=Request({'type':'http','method':'GET','path':'/api/precision-teachings/pt/lesson-materials/d/file','headers':[]})
        downstream=AsyncMock(return_value=JSONResponse({}))
        with patch.object(main,'current_account',return_value=None):
            response=asyncio.run(main.protect_teacher_api(request,downstream))
            self.assertEqual(response.status_code,401)
        with patch.object(main,'current_account',return_value={'must_change_password':False,'role':'teacher','workspace_id':'other'}):
            response=asyncio.run(main.protect_teacher_api(request,downstream))
            self.assertEqual(response.status_code,404)
        downstream.assert_not_called()

    def test_goal_generation_receives_current_lesson_without_replacing_diagnostic_content(self):
        from backend.app import goal_path_api
        state=self.upload();api.confirm_materials('pt',api.Review(revision=state['revision'],text='唐朝繁荣，保留史料比较'))
        skill=load_skill('precision_intervention_goal_path_design')
        sample=json.loads((skill.path.parent/'tests/sample-input.json').read_text())
        report={'generated_json':json.dumps(sample['diagnosis_bundle']['class_diagnosis']),'skill_version':'test'}
        with patch.object(goal_path_api,'_source_report',return_value=report):
            payload,_,_=goal_path_api._build_input('pt','class',sample['teacher_instructional_context'])
        self.assertIn('唐朝繁荣',payload['current_lesson_materials']['text'])
        self.assertEqual(payload['precision_teaching_context']['precision_teaching_content'],['角与向量'])
        self.assertIn('class_diagnosis',payload['diagnosis_bundle'])
