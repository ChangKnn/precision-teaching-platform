"""Stage confirmation must not impersonate individual report review."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend.app import database as db
from backend.app import main, student_api


class FeedbackStageCountTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        configured = SimpleNamespace(database_path=Path(folder.name) / 'feedback.db', seed_demo_data=False)
        patched = patch.object(db, 'settings', configured)
        patched.start()
        self.addCleanup(patched.stop)
        db.initialize_database()
        now = db.utc_now()
        with db.database() as connection:
            connection.execute("INSERT INTO precision_teachings (id,title,goal,subject,grade,created_at,updated_at) VALUES ('t','主题','目标','数学','高一',?,?)", (now, now))
            connection.execute("INSERT INTO classrooms (id,name,created_at,updated_at) VALUES ('c','测试班',?,?)", (now, now))
            connection.execute("INSERT INTO diagnosis_assignments (teaching_id,classroom_id,is_published,assigned_at) VALUES ('t','c',1,?)", (now,))
            connection.execute("INSERT INTO feedback_summaries (teaching_id,status,has_data,total_students,confirmed_count,updated_at) VALUES ('t','pending',1,2,2,?)", (now,))
            for index, status in enumerate(('draft', 'confirmed'), 1):
                sid = f's{index}'
                connection.execute("INSERT INTO students (id,classroom_id,school_name,class_name,name,created_at,updated_at) VALUES (?,'c','测试学校','测试班',?,?,?)", (sid, f'模拟{index}', now, now))
                connection.execute("INSERT INTO student_task_sessions (id,student_id,teaching_id,status,started_at,last_active_at,submitted_at) VALUES (?,?,'t','submitted',?,?,?)", (sid, sid, now, now, now))
                connection.execute("INSERT INTO student_diagnosis_reports (session_id,teaching_id,skill_key,skill_version,provider,status,input_hash,generated_json,report_text,generated_at,updated_at) VALUES (?,'t','solo_student_diagnosis_feedback','1','openrouter',?,'hash','{}','报告',?,?)", (sid, status, now, now))

    def summary(self):
        return db.fetch_one("SELECT * FROM feedback_summaries WHERE teaching_id = 't'")

    def test_both_refresh_paths_count_actual_reviewed_reports(self):
        for refresh in (main.refresh_feedback_counts, student_api.refresh_feedback_counts):
            with self.subTest(refresh=refresh.__module__):
                refresh('t')
                self.assertEqual(self.summary()['confirmed_count'], 1)
                self.assertEqual(self.summary()['total_students'], 2)

    def test_confirming_stage_does_not_confirm_individual_reports(self):
        main.refresh_feedback_counts('t')
        current = {'stages': {'feedback': {'accessible': True}}, 'feedback': self.summary()}
        with patch.object(main, 'workspace_for', return_value=current):
            main.confirm_feedback('t')
        self.assertEqual(self.summary()['status'], 'confirmed')
        self.assertEqual(self.summary()['confirmed_count'], 1)
        statuses = db.fetch_all('SELECT status FROM student_diagnosis_reports ORDER BY session_id')
        self.assertEqual([row['status'] for row in statuses], ['draft', 'confirmed'])

    def test_review_counts_refresh_even_after_stage_confirmation(self):
        with db.database() as connection:
            connection.execute("UPDATE feedback_summaries SET status = 'confirmed'")
            connection.execute("UPDATE student_diagnosis_reports SET status = 'pushed' WHERE session_id = 's1'")
        student_api.refresh_feedback_counts('t')
        self.assertEqual(self.summary()['confirmed_count'], 2)
        with db.database() as connection:
            connection.execute("UPDATE student_diagnosis_reports SET status = 'draft' WHERE session_id = 's1'")
        main.refresh_feedback_counts('t')
        self.assertEqual(self.summary()['confirmed_count'], 1)
        self.assertEqual(self.summary()['status'], 'confirmed')

    def test_mock_or_unpublished_reports_do_not_count(self):
        with db.database() as connection:
            connection.execute("UPDATE student_diagnosis_reports SET provider = 'mock', status = 'confirmed'")
        main.refresh_feedback_counts('t')
        self.assertEqual(self.summary()['confirmed_count'], 0)
        with db.database() as connection:
            connection.execute("UPDATE diagnosis_assignments SET is_published = 0")
        main.refresh_feedback_counts('t')
        self.assertEqual(self.summary()['total_students'], 0)
        self.assertEqual(self.summary()['has_data'], 0)


if __name__ == '__main__':
    unittest.main()
