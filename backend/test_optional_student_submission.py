"""A final written answer is optional when a student submits a dialogue."""

import unittest
from unittest.mock import MagicMock, patch

from backend.app.schemas import StudentTaskSubmit
from backend.app import student_api


class OptionalStudentSubmissionTest(unittest.TestCase):
    def test_empty_text_is_valid(self):
        self.assertEqual(StudentTaskSubmit().submitted_text, "")
        self.assertEqual(StudentTaskSubmit(submitted_text="").submitted_text, "")

    def test_empty_text_still_submits_session(self):
        session = {"id": "session-1", "status": "active", "teaching_id": "teaching-1"}
        connection = MagicMock()
        database = MagicMock()
        database.return_value.__enter__.return_value = connection
        with (
            patch.object(student_api, "owned_session", return_value=session),
            patch.object(student_api, "database", database),
            patch.object(student_api, "refresh_feedback_counts"),
            patch.object(student_api, "write_audit"),
            patch.object(student_api, "utc_now", return_value="2026-09-26T00:00:00Z"),
            patch.object(student_api, "serialize_session", return_value={"status": "submitted", "submitted_text": ""}),
        ):
            result = student_api.submit_task("session-1", StudentTaskSubmit(), {"id": "student-1"})

        self.assertEqual(result["status"], "submitted")
        self.assertEqual(connection.execute.call_args_list[0].args[1][0], "")


if __name__ == "__main__":
    unittest.main()
