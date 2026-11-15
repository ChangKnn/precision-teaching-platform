import io
import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from pypdf import PdfReader

from backend.app.student_api import download_class_feedback_pdf
from backend.app.services.class_report_pdf import render_class_feedback_pdf
from backend.app.services.compact_outputs import practical_grouping

ROOT = Path(__file__).parent / 'skills/solo-class-diagnosis-intervention/tests'


def sample():
    result = json.loads((ROOT/'sample-output.json').read_text())
    for row in result['level_analyses']:
        row['teaching_design'] = {'content':'提供当前任务的材料。', 'task':'比较不同解释，标明每个判断的证据。', 'scaffold':'提供比较维度，让学生独立判断。'}
        row['student_cases'] = [{'student_name':row['students'][0]['student_name'], 'level_reason':'能够联系材料说明判断。', 'obstacle':'关键一步的依据尚未说明。'}]
    return {'result':practical_grouping(result),'status':'ready','generated_at':'2026-11-15 10:00:00'}


class ClassFeedbackPdfTest(unittest.TestCase):
    def test_export_is_protected_by_teacher_and_classroom_scope(self):
        from backend.app.main import protect_teacher_api
        from backend.test_teacher_auth import request
        from starlette.responses import Response
        path='/api/precision-teachings/teaching/classrooms/classroom/class-report.pdf'
        async def next_response(_):
            return Response(status_code=204)
        with patch('backend.app.main.current_account', return_value=None):
            self.assertEqual(asyncio.run(protect_teacher_api(request(path),next_response)).status_code,401)
        teacher={'role':'teacher','must_change_password':False,'workspace_id':'own'}
        with patch('backend.app.main.current_account',return_value=teacher), patch('backend.app.main.fetch_one',return_value={'workspace_id':'other'}):
            self.assertEqual(asyncio.run(protect_teacher_api(request(path),next_response)).status_code,404)
        with patch('backend.app.main.current_account',return_value=teacher), patch('backend.app.main.fetch_one',side_effect=[{'workspace_id':'own'},None]):
            self.assertEqual(asyncio.run(protect_teacher_api(request(path),next_response)).status_code,404)

    def test_chinese_content_and_saved_failed_report_are_exported(self):
        report=sample();report['status']='failed'
        data=render_class_feedback_pdf(report,{'title':'实验数据与解释','subject':'科学'},{'name':'测试班'})
        reader=PdfReader(io.BytesIO(data))
        text='\n'.join(page.extract_text() for page in reader.pages)
        for expected in ('班级集体反馈','上次已保存报告','学生分层学情','比较不同解释','教学支持','个体关注建议'):
            self.assertIn(expected,text)
        self.assertNotIn('stu-',text)

    def test_long_narrative_and_roster_paginate(self):
        report=sample()
        report['result']['level_analyses'][0]['main_performance']='学生能够提出判断，但尚未独立解释依据。'*500
        data=render_class_feedback_pdf(report,{'title':'分页测试','subject':'数学'},{'name':'测试班'})
        reader=PdfReader(io.BytesIO(data))
        self.assertGreater(len(reader.pages),5)
        self.assertIn('个体关注建议',reader.pages[-1].extract_text())

    @patch('backend.app.services.class_report_pdf.render_class_feedback_pdf',return_value=b'%PDF-test')
    @patch('backend.app.services.class_report.report_row')
    @patch('backend.app.student_api.fetch_all',return_value=[])
    @patch('backend.app.student_api.fetch_one')
    def test_attachment_uses_saved_result_without_regenerating(self, one, all_rows, saved, render):
        one.side_effect=[{'title':'反馈主题','subject':'历史'},{'name':'测试班'}]
        saved.return_value=sample();saved.return_value['status']='failed'
        response=download_class_feedback_pdf('teaching','classroom')
        self.assertEqual(response.media_type,'application/pdf')
        self.assertEqual(response.body,b'%PDF-test')
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertIn('filename*=UTF-8',response.headers['content-disposition'])
        self.assertEqual(render.call_args.args[0]['status'],'failed')

    @patch('backend.app.services.class_report.report_row',return_value=None)
    @patch('backend.app.student_api.fetch_one',return_value={'title':'test','name':'class'})
    def test_missing_saved_report_is_clear(self,one,saved):
        with self.assertRaises(HTTPException) as caught:
            download_class_feedback_pdf('t','c')
        self.assertEqual(caught.exception.status_code,409)

    @patch('backend.app.student_api.fetch_one',return_value=None)
    def test_missing_classroom_cannot_export(self,one):
        with self.assertRaises(HTTPException) as caught:
            download_class_feedback_pdf('t','c')
        self.assertEqual(caught.exception.status_code,404)
