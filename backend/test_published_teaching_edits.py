"""Regression checks for edits to a published precision teaching project."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from fastapi import HTTPException
from fastapi import BackgroundTasks

from backend.app import database as database_module
from backend.app.database import database, fetch_one, initialize_database, utc_now
from backend.app.main import update_precision_teaching
from backend.app.schemas import PrecisionTeachingUpdate
from backend.app.services import class_report
from backend.app import custom_analysis_api, goal_path_api


class PublishedTeachingEditTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        test_settings = replace(
            database_module.settings,
            database_path=Path(self.temp.name) / "test.db",
            seed_demo_data=False,
        )
        patcher = patch.object(database_module, "settings", test_settings)
        patcher.start()
        self.addCleanup(patcher.stop)
        initialize_database()
        now = utc_now()
        with database() as connection:
            connection.execute(
                """INSERT INTO teacher_workspaces
                   (id, school_name, subject, teacher_name, identity_key, created_at, updated_at)
                   VALUES ('ws-test', '测试学校', '数学', '测试教师', 'test-key', ?, ?)""",
                (now, now),
            )
            connection.execute(
                """INSERT INTO precision_teachings
                   (id, workspace_id, title, goal, content, rationale, subject, grade,
                    textbook, estimated_periods, status, created_at, updated_at)
                   VALUES ('pt-test', 'ws-test', '原主题', '原目标', '原内容', '', '数学', '高一',
                           '原章节', 1, 'active', ?, ?)""",
                (now, now),
            )
            connection.execute(
                """INSERT INTO diagnosis_tasks
                   (teaching_id, goal, task_text, status, updated_at)
                   VALUES ('pt-test', '原目标', '已发布任务', 'published', ?)""",
                (now,),
            )
            connection.execute(
                """INSERT INTO diagnosis_rubrics
                   (teaching_id, skill_key, skill_version, provider, status, input_hash,
                    generated_json, current_json, generated_at, confirmed_at, updated_at)
                   VALUES ('pt-test', 'solo_task_rubric_builder', '1', 'mock', 'confirmed',
                           'hash', '{}', '{}', ?, ?, ?)""",
                (now, now, now),
            )
        self.request = SimpleNamespace(state=SimpleNamespace(teacher_workspace_id="ws-test"))

    def payload(self, **changes):
        data = dict(title="原主题", goal="原目标", content="原内容", rationale="",
                    subject="数学", grade="高一", textbook="原章节", estimated_periods=1)
        data.update(changes)
        return PrecisionTeachingUpdate(**data)

    def test_published_edit_requires_explicit_wording_confirmation(self):
        with self.assertRaises(HTTPException) as raised:
            update_precision_teaching("pt-test", self.payload(goal="润色后的目标"), self.request)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(fetch_one("SELECT goal FROM precision_teachings WHERE id = 'pt-test'")["goal"], "原目标")

    def test_wording_edit_keeps_published_task_and_confirmed_rubric(self):
        result = update_precision_teaching(
            "pt-test", self.payload(goal="润色后的目标", edit_intent="wording"), self.request
        )
        self.assertEqual(result["goal"], "润色后的目标")
        self.assertEqual(fetch_one("SELECT goal, status FROM diagnosis_tasks WHERE teaching_id = 'pt-test'"),
                         {"goal": "原目标", "status": "published"})
        self.assertEqual(fetch_one("SELECT status FROM diagnosis_rubrics WHERE teaching_id = 'pt-test'")["status"],
                         "confirmed")

    def test_unchanged_published_project_needs_no_confirmation(self):
        self.assertEqual(update_precision_teaching("pt-test", self.payload(), self.request)["title"], "原主题")

    def test_wording_only_change_reuses_validated_class_report(self):
        now = utc_now()
        old_input = {"schema_version": "1.0", "precision_teaching_context": {"goal": "原目标"},
                     "student_diagnosis_results": [{"student": {"student_id": "student-test"}}]}
        new_input = {**old_input, "precision_teaching_context": {"goal": "润色后的目标"}}
        with database() as connection:
            connection.execute(
                "INSERT INTO classrooms (id, name, created_at, updated_at) VALUES ('class-test', '测试班', ?, ?)",
                (now, now),
            )
            connection.execute(
                """INSERT INTO class_diagnosis_reports
                   (teaching_id, classroom_id, skill_key, skill_version, provider, status,
                    source_hash, generated_json, report_text, generated_at, updated_at)
                   VALUES ('pt-test', 'class-test', 'solo_class_diagnosis_intervention', '1',
                           'mock', 'failed', 'old-hash', '{"validated":true}', '原报告', ?, ?)""",
                (now, now),
            )
            connection.execute(
                """INSERT INTO ai_jobs
                   (id, skill_key, skill_version, provider, status, input_json, created_at, completed_at)
                   VALUES ('job-test', 'solo_class_diagnosis_intervention', '1', 'mock',
                           'completed', ?, ?, ?)""",
                (json.dumps(old_input), now, now),
            )
            connection.execute(
                """INSERT INTO goal_path_analysis_drafts
                   (teaching_id, classroom_id, class_source_hash, content_analysis,
                    focus_analysis, provider, updated_at)
                   VALUES ('pt-test', 'class-test', 'old-hash', '原分析', '原重点', 'mock', ?)""",
                (now,),
            )
        with patch.object(class_report, "class_report_input", return_value=(new_input, "new-hash", {})), \
             patch.object(class_report, "load_skill", return_value=SimpleNamespace(key="solo_class_diagnosis_intervention", version="1")):
            result = class_report.queue_class_report("pt-test", "class-test", BackgroundTasks())
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["source_hash"], "new-hash")
        self.assertEqual(result["report_text"], "原报告")
        self.assertEqual(fetch_one("SELECT class_source_hash FROM goal_path_analysis_drafts WHERE teaching_id = 'pt-test'")["class_source_hash"], "new-hash")

    def test_wording_only_change_keeps_custom_analysis_ready(self):
        now = utc_now()
        old_input = {"mode": "class", "teacher_criteria": "原标准", "teaching_context": {"goal": "原目标"},
                     "students": [{"student_id": "student-test"}]}
        new_input = {**old_input, "teaching_context": {"goal": "润色后的目标"}}
        with database() as connection:
            connection.execute(
                """INSERT INTO teacher_analysis_reports
                   (teaching_id, scope, subject_id, skill_key, skill_version, provider,
                    input_hash, result_json, generated_at)
                   VALUES ('pt-test', 'class', 'class-test', 'teacher_custom_analysis',
                           '1', 'mock', 'old-hash', '{"summary":"原报告"}', ?)""",
                (now,),
            )
            connection.execute(
                """INSERT INTO ai_jobs
                   (id, skill_key, skill_version, provider, status, input_json, created_at, completed_at)
                   VALUES ('job-custom', 'teacher_custom_analysis', '1', 'mock',
                           'completed', ?, ?, ?)""",
                (json.dumps(old_input), now, now),
            )
        with patch.object(custom_analysis_api, "build_custom_input", return_value=(new_input, "new-hash")):
            result = custom_analysis_api.custom_report_for("pt-test", "class", "class-test")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["result"], {"summary": "原报告"})
        self.assertEqual(fetch_one("SELECT input_hash FROM teacher_analysis_reports WHERE teaching_id = 'pt-test'")["input_hash"], "new-hash")

    def test_updated_class_report_keeps_prior_teacher_analysis_visible_for_review(self):
        now = utc_now()
        with database() as connection:
            connection.execute(
                "INSERT INTO classrooms (id, name, created_at, updated_at) VALUES ('class-test', '测试班', ?, ?)",
                (now, now),
            )
            connection.execute(
                """INSERT INTO class_diagnosis_reports
                   (teaching_id, classroom_id, skill_key, skill_version, provider, status,
                    source_hash, generated_json, report_text, generated_at, updated_at)
                   VALUES ('pt-test', 'class-test', 'solo_class_diagnosis_intervention', '1',
                           'mock', 'ready', 'new-hash', '{}', '新报告', ?, ?)""",
                (now, now),
            )
            connection.execute(
                """INSERT INTO goal_path_analysis_drafts
                   (teaching_id, classroom_id, class_source_hash, content_analysis,
                    focus_analysis, source_note, content_confirmed, focus_confirmed,
                    provider, updated_at)
                   VALUES ('pt-test', 'class-test', 'old-hash', '原分析', '原重点',
                           '原提示', 1, 1, 'mock', ?)""",
                (now,),
            )
        draft = goal_path_api._analysis_draft('pt-test', 'class-test')
        self.assertEqual((draft['content_analysis'], draft['focus_analysis']), ('原分析', '原重点'))
        self.assertTrue(draft['stale'])
        self.assertFalse(draft['content_confirmed'])
        self.assertFalse(draft['focus_confirmed'])
        self.assertIn('核对', draft['source_note'])
