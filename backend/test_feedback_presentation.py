"""Short student feedback and complete, non-overlapping group recommendations."""

import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend.app import database as database_module, student_api
from backend.app.services.class_report import validate_class_result
from backend.app.student_api import student_feedback_for_row
from backend.app.schemas import StudentDiagnosisReportUpdate


ROOT = Path(__file__).resolve().parent / "skills" / "solo-class-diagnosis-intervention" / "tests"


class FeedbackPresentationTest(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads((ROOT / "sample-input.json").read_text(encoding="utf-8"))
        self.result = json.loads((ROOT / "sample-output.json").read_text(encoding="utf-8"))

    def test_complete_alternative_groups_are_accepted(self):
        validate_class_result(copy.deepcopy(self.result), self.payload)

    def test_missing_student_in_either_scheme_is_rejected(self):
        for scheme in ("homogeneous_groups", "heterogeneous_groups"):
            with self.subTest(scheme=scheme):
                result = copy.deepcopy(self.result)
                result["grouping_recommendations"][scheme][-1]["students"].pop()
                with self.assertRaisesRegex(ValueError, "未覆盖"):
                    validate_class_result(result, self.payload)

    def test_no_heterogeneous_scheme_requires_reason(self):
        result = copy.deepcopy(self.result)
        result["grouping_recommendations"]["heterogeneous_groups"] = []
        result["grouping_recommendations"]["heterogeneous_not_recommended_reason"] = "当前缺少有价值的互补任务。"
        validate_class_result(result, self.payload)
        result["grouping_recommendations"]["heterogeneous_not_recommended_reason"] = None
        with self.assertRaisesRegex(ValueError, "必须说明原因"):
            validate_class_result(result, self.payload)

    def test_legacy_teacher_report_is_not_sent_to_students(self):
        result = self.payload["student_diagnosis_results"][0]
        row = {"student_feedback_text": "", "generated_json": json.dumps(result, ensure_ascii=False),
               "report_text": "教师专用的完整报告"}
        feedback = student_feedback_for_row(row)
        self.assertIn("这次的学习表现", feedback)
        self.assertIn("你的作答告诉了我们什么", feedback)
        self.assertIn("老师说温度高蒸发就快", feedback)
        self.assertIn("你现在的思路", feedback)
        self.assertIn("接下来要突破什么", feedback)
        self.assertIn("下一步目标", feedback)
        self.assertIn("可以怎样练", feedback)
        self.assertNotIn("教师专用的完整报告", feedback)
        self.assertNotIn("P（前结构）", feedback)
        self.assertGreater(len(feedback), 250)
        self.assertLess(len(feedback), 5000)

    def test_legacy_student_report_keeps_distinct_evidence_and_strategies(self):
        result = copy.deepcopy(self.payload["student_diagnosis_results"][0])
        result["diagnosis"]["evidence"].append({
            "quote": "我又看到了第二条实验线索。",
            "interpretation": "学生补充了另一条可核对的原话。",
        })
        result["strategies"].append({
            "title": "对照两条线索",
            "action": "把两条实验线索分别写出，并说明它们怎样支持判断。",
        })
        feedback = student_feedback_for_row({
            "student_feedback_text": "", "generated_json": json.dumps(result, ensure_ascii=False),
        })
        self.assertIn("我又看到了第二条实验线索", feedback)
        self.assertIn("对照两条线索", feedback)
        self.assertIn("把两条实验线索分别写出", feedback)

    def test_student_receives_only_reviewed_short_feedback(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(
            database_module, "settings", SimpleNamespace(
                database_path=Path(folder) / "feedback.db", seed_demo_data=False,
            ),
        ):
            database_module.initialize_database()
            now = database_module.utc_now()
            with database_module.database() as connection:
                connection.execute("""INSERT INTO precision_teachings
                    (id, title, goal, subject, grade, created_at, updated_at)
                    VALUES ('t1', '测试主题', '测试目标', '数学', '高一', ?, ?)""", (now, now))
                connection.execute("""INSERT INTO classrooms (id, name, created_at, updated_at)
                    VALUES ('c1', '高一1班', ?, ?)""", (now, now))
                connection.execute("""INSERT INTO students
                    (id, classroom_id, school_name, class_name, name, created_at, updated_at)
                    VALUES ('s1', 'c1', '学校', '高一1班', '学生', ?, ?)""", (now, now))
                connection.execute("""INSERT INTO student_task_sessions
                    (id, student_id, teaching_id, status, started_at, last_active_at, submitted_at)
                    VALUES ('ss1', 's1', 't1', 'submitted', ?, ?, ?)""", (now, now, now))
                connection.execute("""INSERT INTO student_diagnosis_reports
                    (session_id, teaching_id, skill_key, skill_version, provider, status,
                     input_hash, generated_json, report_text, generated_at, updated_at)
                    VALUES ('ss1', 't1', 'solo_student_diagnosis_feedback', '1.2.0', 'openrouter',
                    'draft', 'hash', ?, '教师的详细报告', ?, ?)""", (
                        json.dumps(self.payload["student_diagnosis_results"][0], ensure_ascii=False), now, now,
                    ))
            student_api.update_student_report("ss1", StudentDiagnosisReportUpdate(
                report_text="教师的详细报告", student_feedback_text="### 你已经做到\n你找到了一个线索。",
                status="pushed",
            ))
            row = database_module.fetch_one("SELECT * FROM student_task_sessions WHERE id = 'ss1'")
            student_view = student_api.serialize_session(row)["report"]
            teacher_view = student_api.serialize_student_report("ss1")
            self.assertEqual(student_view["report_text"], "### 你已经做到\n你找到了一个线索。")
            self.assertNotIn("generated_json", student_view)
            self.assertNotIn("student_feedback_text", student_view)
            self.assertEqual(teacher_view["report_text"], "教师的详细报告")


if __name__ == "__main__":
    unittest.main()
