"""PDF export checks for the integrated teaching report."""

import json
import copy
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from backend.app.intervention_integration_api import download_integration_report_pdf
from backend.app.services.report_pdf import _markup, render_report_pdf


SAMPLE = Path(__file__).parent / "skills/precision-intervention-plan-integration-review/tests/sample-output.json"


class ReportPdfTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = json.loads(SAMPLE.read_text(encoding="utf-8"))

    def test_escapes_report_text(self):
        self.assertEqual(_markup('<script>"&"</script>'), '&lt;script&gt;"&amp;"&lt;/script&gt;')

    def test_generates_pdf_from_structured_report(self):
        content = render_report_pdf(self.result)
        self.assertTrue(content.startswith(b"%PDF-"))
        self.assertGreater(len(content), 10000)

    def test_long_table_cell_can_continue_on_next_page(self):
        result = copy.deepcopy(self.result)
        context = result["integrated_plan"]["teaching_context_and_conditions"]
        context["teaching_content_and_curriculum_analysis"] *= 100
        self.assertTrue(render_report_pdf(result).startswith(b"%PDF-"))

    @patch("reportlab.pdfbase.pdfmetrics.getRegisteredFontNames", return_value=[])
    @patch("reportlab.pdfbase.ttfonts.TTFont", side_effect=ValueError("unsupported font"))
    @patch("backend.app.services.report_pdf.Path.is_file", return_value=True)
    def test_unsupported_installed_fonts_fall_back(self, _exists, font_loader, _registered):
        self.assertTrue(render_report_pdf(self.result).startswith(b"%PDF-"))
        self.assertGreater(font_loader.call_count, 0)

    @patch("reportlab.pdfbase.pdfmetrics.getRegisteredFontNames", return_value=[])
    @patch("reportlab.pdfbase.ttfonts.TTFont", side_effect=ValueError("test fallback"))
    @patch("backend.app.services.report_pdf.Path.is_file", return_value=True)
    @patch.dict("os.environ", {"WINDIR": "D:/Windows", "PDF_CJK_FONT_PATH": ""})
    def test_tries_windows_chinese_fonts(self, _exists, font_loader, _registered):
        render_report_pdf(self.result)
        paths = [call.args[1].replace("\\", "/") for call in font_loader.call_args_list]
        self.assertIn("D:/Windows/Fonts/simsun.ttc", paths)
        self.assertIn("D:/Windows/Fonts/simhei.ttf", paths)

    @patch("backend.app.intervention_integration_api._source", return_value=({}, "new-hash", "1.0"))
    @patch("backend.app.intervention_integration_api._saved")
    def test_rejects_stale_report(self, saved, _source):
        saved.return_value = {"status": "stale", "result": self.result}
        with self.assertRaises(HTTPException) as caught:
            download_integration_report_pdf("teaching", "classroom")
        self.assertEqual(caught.exception.status_code, 409)

    @patch("backend.app.intervention_integration_api._source", return_value=({}, "hash", "1.0"))
    @patch("backend.app.intervention_integration_api._saved")
    @patch("backend.app.intervention_integration_api.render_report_pdf", return_value=b"%PDF-sample")
    def test_download_is_pdf_attachment(self, _render, saved, _source):
        saved.return_value = {"status": "current", "result": self.result}
        response = download_integration_report_pdf("teaching", "classroom")
        self.assertEqual(response.media_type, "application/pdf")
        self.assertEqual(response.body, b"%PDF-sample")
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertEqual(response.headers["cache-control"], "no-store")
