"""Visible task files/links, evidence context and versioned access."""
import asyncio,io,json
from urllib.parse import quote
from unittest import TestCase
from unittest.mock import patch

from docx import Document
from fastapi import HTTPException,Request
from reportlab.pdfgen import canvas
from backend import test_task_version_runtime as runtime_tests
from backend.app import task_attachments as attachments,task_materials as materials,student_api,database as db
from backend.app.services import task_versions as versions


class TaskAttachmentsTest(TestCase):
    def setUp(self):
        self.case=runtime_tests.TaskVersionRuntimeTest('test_unchanged_publish_does_not_create_spurious_versions')
        self.case.setUp();self.addCleanup(self.case.doCleanups)
        p=patch.object(attachments,'settings',self.case.settings);p.start();self.addCleanup(p.stop)

    def upload(self,name='material.txt',body=b'Task source evidence',source_hash=None):
        async def receive():return {'type':'http.request','body':body,'more_body':False}
        request=Request({'type':'http','method':'POST','path':'/api/precision-teachings/pt/materials/files','headers':[
            (b'x-file-name',quote(name).encode()),(b'x-material-version',(source_hash or materials.material_state('pt')['source_hash']).encode())]},receive)
        return asyncio.run(attachments.upload_task_file('pt',request))

    def link(self,url='https://example.org/article',text=''):
        return attachments.add_web_attachment('pt',attachments.WebAttachment(url=url,name='网页材料',content_text=text,source_hash=materials.material_state('pt')['source_hash']))

    def test_txt_pdf_docx_are_visible_materials_and_reach_ai_context(self):
        doc=Document();doc.add_paragraph('材料中的案例事实');docstream=io.BytesIO();doc.save(docstream)
        pdfstream=io.BytesIO();pdf=canvas.Canvas(pdfstream);pdf.drawString(20,700,'PDF case evidence.');pdf.save()
        for name,body in [('a.txt','教师提供的材料'.encode()),('b.docx',docstream.getvalue()),('c.pdf',pdfstream.getvalue())]:self.upload(name,body)
        state=materials.material_state('pt');self.assertEqual(len(state['attachments']),3)
        self.assertIn('材料中的案例事实',materials.canonical_task_text('pt'))
        self.case.confirm_rubric();self.case.publish()
        v=versions.published_version('pt');task=versions.version_task(v)
        self.assertEqual(len(task['task_attachments']),3)
        self.assertNotIn('材料中的案例事实',task['task_text'])
        self.assertIn('材料中的案例事实',versions.version_config(v)['diagnostic_task']['task_content'])
        self.assertNotIn('stored_filename',json.dumps(task))
        self.assertNotIn('role_knowledge',json.dumps(task))

    def test_web_links_are_not_fetched_and_missing_body_is_explicit(self):
        with patch('urllib.request.urlopen',side_effect=AssertionError('Must not fetch arbitrary web links')):
            self.link()
        self.assertIn('未提供网页正文',materials.canonical_task_text('pt'))
        self.link('https://example.org/second','教师提供的网页节选')
        self.assertIn('教师提供的网页节选',materials.canonical_task_text('pt'))

    def test_invalid_url_protocols_credentials_and_control_chars_are_rejected(self):
        for url in ('javascript:alert(1)','data:text/html,hello','ftp://example.org','https://user:password@example.org','https://example.org/a\nb','http://'):
            with self.subTest(url=url),self.assertRaises(HTTPException):self.link(url)
        self.assertEqual(materials.material_state('pt')['attachments'],[])

    def test_file_versions_survive_replacement_and_removal(self):
        state=self.upload();item=state['attachments'][0]
        self.case.confirm_rubric();self.case.publish()
        sid=self.case.start()['session']['id'];self.case.send(sid)
        old=versions.published_version('pt');before=db.fetch_all('SELECT * FROM student_messages ORDER BY id')
        attachments.remove_attachment('pt',item['id'],materials.MaterialConfirmation(source_hash=materials.material_state('pt')['source_hash']))
        self.case.confirm_rubric();self.case.publish()
        response=student_api.student_version_attachment('pt',old['id'],item['id'],self.case.student)
        self.assertTrue(response.path.is_file())
        self.assertEqual(db.fetch_all('SELECT * FROM student_messages ORDER BY id'),before)
        self.assertEqual(versions.version_task(versions.published_version('pt'))['task_attachments'],[])
        with self.assertRaises(HTTPException):student_api.student_version_attachment('pt',old['id'],item['id'],{**self.case.student,'id':'other','classroom_id':'other'})

    def test_duplicate_stale_upload_and_wrong_task_deletion_are_rejected(self):
        old=materials.material_state('pt')['source_hash'];state=self.upload()
        with self.assertRaises(HTTPException):self.upload()
        with self.assertRaises(HTTPException):self.upload('different.txt',b'new',old)
        with self.assertRaises(HTTPException):attachments.remove_attachment('other',state['attachments'][0]['id'],materials.MaterialConfirmation(source_hash=materials.material_state('other')['source_hash']))
        self.assertEqual(len(materials.material_state('pt')['attachments']),1)

    def test_size_format_and_document_limits(self):
        with patch.object(attachments,'MAX_BYTES',4):
            with self.assertRaises(HTTPException):self.upload(body=b'12345')
        with self.assertRaises(HTTPException):self.upload('unsafe.html',b'<script>bad</script>')
        with patch.object(attachments,'MAX_ATTACHMENTS',1):
            self.upload()
            with self.assertRaises(HTTPException):self.link()
