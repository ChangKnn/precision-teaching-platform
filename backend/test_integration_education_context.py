"""University courses allow an empty subject and retain teacher-owned metadata."""
import copy
import json
from io import BytesIO
from pathlib import Path
from unittest import TestCase

from docx import Document
from jsonschema import validate, ValidationError

from backend.app.intervention_integration_api import _restore_confirmed_teaching_information, _validate_output
from backend.app.services.report_docx import render_report_docx

ROOT = Path(__file__).parent / 'skills/precision-intervention-plan-integration-review'


class IntegrationEducationContextTest(TestCase):
    def setUp(self):
        self.source = json.loads((ROOT / 'tests/sample-input.json').read_text(encoding='utf-8'))
        self.result = json.loads((ROOT / 'tests/sample-output.json').read_text(encoding='utf-8'))
        self.schema = json.loads((ROOT / 'assets/plan-integration-output.schema.json').read_text(encoding='utf-8'))

    def university(self):
        self.source['precision_teaching_context'].update(education_stage='高等教育', subject='', course_name='中学教育学基础', major='教育学', grade='大二')

    def test_university_subject_is_restored_from_source_and_valid(self):
        self.university()
        self.result['integrated_plan']['basic_information']['subject'] = '（高等教育课程，学科字段未填写）'
        before = copy.deepcopy(self.result)
        fixed = _restore_confirmed_teaching_information(self.source, self.result)
        validate(fixed, self.schema)
        _validate_output(self.source, fixed)
        self.assertEqual(fixed['integrated_plan']['basic_information']['subject'], '')
        self.assertEqual(fixed['integrated_plan']['basic_information']['course_name'], '中学教育学基础')
        self.assertEqual(self.result, before)

    def test_school_subject_still_required_and_model_cannot_change_it(self):
        self.result['integrated_plan']['basic_information']['subject'] = ''
        with self.assertRaises(ValidationError):
            validate(self.result, self.schema)
        self.result['integrated_plan']['basic_information'].update(education_stage='高等教育', course_name='错误课程')
        fixed = _restore_confirmed_teaching_information(self.source, self.result)
        self.assertNotIn('education_stage', fixed['integrated_plan']['basic_information'])
        self.assertEqual(fixed['integrated_plan']['basic_information']['subject'], self.source['precision_teaching_context']['subject'])
        validate(fixed, self.schema)
        _validate_output(self.source, fixed)

    def test_university_word_export_uses_course_and_major(self):
        self.university()
        fixed = _restore_confirmed_teaching_information(self.source, self.result)
        document = Document(BytesIO(render_report_docx(fixed)))
        table = document.tables[0]
        self.assertEqual(table.cell(1, 0).text, '课程名称')
        self.assertEqual(table.cell(1, 1).text, '中学教育学基础')
        self.assertEqual(table.cell(2, 0).text, '专业')
        self.assertEqual(table.cell(2, 1).text, '教育学')
