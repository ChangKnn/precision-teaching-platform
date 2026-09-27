"""Checks the installed diagnostic-task Skill and API input mapping without calling an LLM."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from backend.app.diagnostic_task_design_api import _source
from backend.app.services.ai import run_diagnostic_task_design
from backend.app.services.skills import load_skill


SKILL = Path(__file__).parent / "skills/precision-diagnostic-task-design"


class DiagnosticTaskDesignTest(unittest.TestCase):
    def test_input_uses_only_precision_teaching_context(self):
        teaching = {
            "title": "数学建模", "goal": "解释数量关系", "content": "条件分析\n推理说明",
            "subject": "数学", "grade": "高一", "textbook": "必修第一册",
        }
        with patch("backend.app.diagnostic_task_design_api.fetch_one", return_value=teaching):
            payload, source_hash, version = _source("pt-1")
        context = payload["precision_teaching_context"]
        self.assertEqual(set(payload), {"precision_teaching_context"})
        self.assertEqual(context["precision_teaching_content"], ["条件分析", "推理说明"])
        self.assertEqual(version, "1.0.0")
        self.assertEqual(len(source_hash), 64)

    def test_missing_teaching_content_is_rejected(self):
        teaching = {"title": "主题", "goal": "目标", "content": ""}
        with patch("backend.app.diagnostic_task_design_api.fetch_one", return_value=teaching):
            with self.assertRaises(HTTPException) as caught:
                _source("pt-1")
        self.assertEqual(caught.exception.status_code, 422)

    def test_sample_output_passes_installed_skill_contract(self):
        skill = load_skill("precision_diagnostic_task_design")
        sample_input = json.loads((SKILL / "tests/sample-input.json").read_text(encoding="utf-8"))
        sample_output = json.loads((SKILL / "tests/sample-output.json").read_text(encoding="utf-8"))
        with patch("backend.app.services.ai._run_structured_skill", return_value=sample_output):
            result = run_diagnostic_task_design(skill, sample_input, "deepseek")
        self.assertEqual(len(result["diagnostic_task_design"]["candidates"]), 3)


if __name__ == "__main__":
    unittest.main()
