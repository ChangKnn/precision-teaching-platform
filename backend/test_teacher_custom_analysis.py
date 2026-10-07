"""Teacher-defined analysis stays independent from SOLO and cites real student evidence."""

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi import BackgroundTasks

from backend.app import custom_analysis_api, database as database_module, student_api
from backend.app.schemas import TeacherAnalysisStandardUpdate
from backend.app.services import ai
from backend.app.services.skills import load_skill


class TeacherCustomAnalysisTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        db_patch = patch.object(database_module, "settings", SimpleNamespace(
            database_path=Path(folder.name) / "custom.db", seed_demo_data=False,
        ))
        db_patch.start()
        self.addCleanup(db_patch.stop)
        database_module.initialize_database()
        now = database_module.utc_now()
        with database_module.database() as connection:
            connection.execute(
                """INSERT INTO precision_teachings
                   (id, title, goal, content, subject, grade, created_at, updated_at)
                   VALUES ('teaching-1', '数学论证', '说清依据', '几何', '数学', '高一', ?, ?)""",
                (now, now),
            )
            connection.execute(
                """INSERT INTO diagnosis_tasks
                   (teaching_id, task_text, status, updated_at)
                   VALUES ('teaching-1', '说明角度求解的理由', 'published', ?)""",
                (now,),
            )
            connection.execute(
                """INSERT INTO classrooms (id, name, created_at, updated_at)
                   VALUES ('class-1', '高一（1）班', ?, ?)""",
                (now, now),
            )
            connection.execute(
                """INSERT INTO diagnosis_assignments
                   (teaching_id, classroom_id, assigned_at, is_published)
                   VALUES ('teaching-1', 'class-1', ?, 1)""",
                (now,),
            )
            connection.execute(
                """INSERT INTO students
                   (id, classroom_id, school_name, class_name, name, created_at, updated_at)
                   VALUES ('student-1', 'class-1', '学校', '高一（1）班', '小明', ?, ?)""",
                (now, now),
            )
            connection.execute(
                """INSERT INTO student_task_sessions
                   (id, student_id, teaching_id, status, started_at, last_active_at, submitted_text, submitted_at)
                   VALUES ('session-1', 'student-1', 'teaching-1', 'submitted', ?, ?, '我比较了两种方法。', ?)""",
                (now, now, now),
            )
            connection.execute(
                """INSERT INTO student_messages (session_id, role, content, created_at)
                   VALUES ('session-1', 'student', '我先画出垂线，再计算角度。', ?)""",
                (now,),
            )

    def test_scope_requires_criteria_and_reports_go_stale_after_edit(self):
        with self.assertRaises(HTTPException) as empty:
            custom_analysis_api.save_standard(
                "teaching-1", TeacherAnalysisStandardUpdate(individual_enabled=True)
            )
        self.assertEqual(empty.exception.status_code, 422)
        custom_analysis_api.save_standard("teaching-1", TeacherAnalysisStandardUpdate(
            criteria="说明方法选择的理由", individual_enabled=True, class_enabled=True,
        ))
        payload, _ = custom_analysis_api.build_custom_input("teaching-1", "individual", "session-1")
        self.assertEqual(payload["mode"], "individual")
        self.assertEqual(len(payload["students"]), 1)
        self.assertNotIn("小明", str(payload))
        with patch.object(custom_analysis_api, "settings", SimpleNamespace(ai_provider="mock")):
            report = custom_analysis_api.generate_custom_report("teaching-1", "individual", "session-1")["report"]
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["result"]["findings"], [])
        with patch.object(custom_analysis_api, "settings", SimpleNamespace(ai_provider="mock")):
            class_report = custom_analysis_api.generate_custom_report("teaching-1", "class", "class-1")["report"]
        self.assertEqual(class_report["result"]["analyzed_student_count"], 1)
        with patch.object(student_api, "queue_class_report", return_value=None):
            results = student_api.student_results("teaching-1", BackgroundTasks())
        self.assertEqual(results["results"][0]["custom_analysis"]["status"], "ready")
        self.assertEqual(results["custom_class_reports"]["class-1"]["status"], "ready")
        custom_analysis_api.save_standard("teaching-1", TeacherAnalysisStandardUpdate(
            criteria="说明运算决策的依据", individual_enabled=True, class_enabled=True,
        ))
        self.assertEqual(custom_analysis_api.custom_report_for("teaching-1", "individual", "session-1")["status"], "stale")

    def test_fabricated_quote_is_rejected(self):
        skill = load_skill("teacher_custom_analysis")
        payload = {
            "mode": "individual", "students": [{"student_id": "student-1", "evidence_sources": [
                {"source_id": "turn-1", "role": "student", "content": "我画了垂线"},
            ]}],
        }
        result = {
            "mode": "individual", "analyzed_student_count": 1, "summary": "有观察",
            "findings": [{"criterion": "理由", "finding": "有解释", "evidence": [
                {"source_id": "turn-1", "quote": "我已经完整论证"},
            ]}],
            "suggestions": [], "limitations": [],
        }
        with patch.object(ai, "_run_structured_skill", return_value=result):
            with self.assertRaisesRegex(ValueError, "不存在的学生原话"):
                ai.run_teacher_custom_analysis(skill, payload, "openrouter")


if __name__ == "__main__":
    unittest.main()
