"""Teacher identity entry and API gate regression tests."""

import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException, Request, Response

from backend.app import database as database_module
from backend.app import teacher_auth
from backend.app import student_api
from backend.app.main import bootstrap, create_classroom, create_precision_teaching, create_precision_teaching_draft, protect_teacher_api, update_classroom, update_diagnosis, update_precision_teaching, update_teacher_profile, workspace_for
from backend.app.schemas import ClassroomCreate, ClassroomUpdate, DiagnosisTaskUpdate, PrecisionTeachingCreate, PrecisionTeachingDraft, PrecisionTeachingUpdate, TeacherProfileUpdate, StudentLogin


def request(path: str, *, method: str = "GET", cookie: str = "", origin: str = "") -> Request:
    headers = [(b"host", b"127.0.0.1:8000")]
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    if origin:
        headers.append((b"origin", origin.encode()))
    return Request({
        "type": "http", "method": method, "path": path, "root_path": "", "query_string": b"",
        "scheme": "http", "server": ("127.0.0.1", 8000), "client": ("127.0.0.1", 10000), "headers": headers,
    })


class TeacherAuthTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        db_patch = patch.object(database_module, "settings", SimpleNamespace(database_path=Path(self.folder.name) / "auth.db", seed_demo_data=True))
        db_patch.start()
        self.addCleanup(db_patch.stop)
        database_module.initialize_database()
        with database_module.database() as connection:
            connection.execute(
                "INSERT INTO classrooms (id, name, student_count, background, created_at, updated_at) VALUES (?, ?, 0, '', ?, ?)",
                ("class-1", "高一（3）班", database_module.utc_now(), database_module.utc_now()),
            )
            connection.execute(
                "INSERT OR IGNORE INTO classroom_schools (classroom_id, school_name, updated_at) VALUES (?, ?, ?)",
                ("class-1", "杭州市求知中学", database_module.utc_now()),
            )

    def identity(self, **changes):
        return teacher_auth.TeacherIdentity(**{
            "school_name": "杭州市求知中学", "subject": "数学", "teacher_name": "林老师", **changes,
        })

    def test_draft_update_reuses_project_and_rejects_other_teacher(self):
        login_response = Response()
        teacher_auth.login(self.identity(teacher_name="草稿老师"), request("/api/teacher-auth/login", method="POST"), login_response)
        cookie = login_response.headers["set-cookie"].split(";", 1)[0]
        workspace_id = teacher_auth.current_teacher(request("/api/bootstrap", cookie=cookie))["workspace_id"]
        own_request = request("/api/precision-teachings", method="POST", cookie=cookie)
        own_request.state.teacher_workspace_id = workspace_id
        update_teacher_profile(TeacherProfileUpdate(display_name="草稿老师", subject="数学", years_experience=0), own_request)
        create_classroom(ClassroomCreate(name="高一（9）班", student_count=30), own_request)
        draft = create_precision_teaching_draft(PrecisionTeachingDraft(title="尚未写完"), own_request)
        updated = update_precision_teaching(
            draft["id"], PrecisionTeachingUpdate(title="同一个项目", goal="观察推理过程", content="函数关系"), own_request
        )
        self.assertEqual(updated["id"], draft["id"])
        self.assertEqual(updated["title"], "同一个项目")
        self.assertEqual(len(bootstrap(own_request)["precision_teachings"]), 1)

        other_login = Response()
        teacher_auth.login(self.identity(teacher_name="另一位老师"), request("/api/teacher-auth/login", method="POST"), other_login)
        other_cookie = other_login.headers["set-cookie"].split(";", 1)[0]
        other_id = teacher_auth.current_teacher(request("/api/bootstrap", cookie=other_cookie))["workspace_id"]
        other_request = request("/api/precision-teachings", method="PUT", cookie=other_cookie)
        other_request.state.teacher_workspace_id = other_id
        with self.assertRaises(HTTPException) as denied:
            update_precision_teaching(draft["id"], PrecisionTeachingUpdate(title="越权修改"), other_request)
        self.assertEqual(denied.exception.status_code, 404)

    def test_database_starts_without_demo_data_by_default(self):
        empty_path = Path(self.folder.name) / "empty.db"
        with patch.object(database_module, "settings", SimpleNamespace(database_path=empty_path, seed_demo_data=False)):
            database_module.initialize_database()
            for table in ("teacher_workspaces", "classrooms", "precision_teachings", "students"):
                self.assertEqual(database_module.fetch_one(f"SELECT COUNT(*) AS total FROM {table}")["total"], 0)

    def test_login_options_login_logout_and_session_cookie(self):
        options = teacher_auth.get_login_options(Response())
        self.assertIn("杭州市求知中学", options["schools"])
        self.assertIn("数学", options["subjects"])
        self.assertFalse(teacher_auth.status(request("/api/teacher-auth/status"), Response())["authenticated"])
        login_response = Response()
        teacher_auth.login(self.identity(), request("/api/teacher-auth/login", method="POST"), login_response)
        cookie = login_response.headers["set-cookie"].split(";", 1)[0]
        self.assertIn("HttpOnly", login_response.headers["set-cookie"])
        self.assertIn("SameSite=strict", login_response.headers["set-cookie"])
        self.assertEqual(teacher_auth.current_teacher(request("/api/bootstrap", cookie=cookie))["teacher_name"], "林老师")
        teacher_auth.logout(request("/api/teacher-auth/logout", method="POST", cookie=cookie), Response())
        self.assertIsNone(teacher_auth.current_teacher(request("/api/bootstrap", cookie=cookie)))

    def test_new_identity_creates_isolated_space_and_repeat_reuses_it(self):
        original = Response()
        teacher_auth.login(self.identity(), request("/api/teacher-auth/login", method="POST"), original)
        original_cookie = original.headers["set-cookie"].split(";", 1)[0]
        original_id = teacher_auth.current_teacher(request("/api/bootstrap", cookie=original_cookie))["workspace_id"]

        new_identity = self.identity(teacher_name="陈老师", subject="物理")
        new_response = Response()
        teacher_auth.login(new_identity, request("/api/teacher-auth/login", method="POST"), new_response)
        new_cookie = new_response.headers["set-cookie"].split(";", 1)[0]
        new_id = teacher_auth.current_teacher(request("/api/bootstrap", cookie=new_cookie))["workspace_id"]
        self.assertNotEqual(original_id, new_id)
        self.assertTrue(database_module.fetch_all("SELECT id FROM precision_teachings WHERE workspace_id = ?", (original_id,)))
        self.assertEqual(database_module.fetch_all("SELECT id FROM precision_teachings WHERE workspace_id = ?", (new_id,)), [])

        repeated = Response()
        teacher_auth.login(self.identity(teacher_name=" 陈老师 ", subject="物理"), request("/api/teacher-auth/login", method="POST"), repeated)
        repeated_cookie = repeated.headers["set-cookie"].split(";", 1)[0]
        self.assertEqual(teacher_auth.current_teacher(request("/api/bootstrap", cookie=repeated_cookie))["workspace_id"], new_id)

        new_request = request("/api/bootstrap", cookie=new_cookie)
        new_request.state.teacher_workspace_id = new_id
        self.assertEqual(bootstrap(new_request)["precision_teachings"], [])
        self.assertEqual(bootstrap(new_request)["classrooms"], [])
        self.assertFalse(bootstrap(new_request)["teacher"]["profile_completed"])
        with self.assertRaises(HTTPException) as missing_profile:
            create_precision_teaching(PrecisionTeachingCreate(
                title="力学新课", goal="诊断学生受力分析", content="受力分析", subject="物理",
            ), new_request)
        self.assertEqual(missing_profile.exception.status_code, 422)
        saved = update_teacher_profile(TeacherProfileUpdate(
            display_name="陈老师", subject="物理", years_experience=0, teaching_style=""
        ), new_request)
        self.assertTrue(saved["profile_completed"])
        self.assertTrue(bootstrap(new_request)["teacher"]["profile_completed"])
        with self.assertRaises(HTTPException) as missing_class:
            create_precision_teaching(PrecisionTeachingCreate(
                title="力学新课", goal="诊断学生受力分析", content="受力分析", subject="物理",
            ), new_request)
        self.assertEqual(missing_class.exception.status_code, 422)
        new_classroom = create_classroom(ClassroomCreate(name="高一（6）班", student_count=36), new_request)
        original_request = request("/api/bootstrap", cookie=original_cookie)
        original_request.state.teacher_workspace_id = original_id
        self.assertEqual({item["name"] for item in bootstrap(original_request)["classrooms"]}, {"高一（3）班", "高一（5）班"})
        created = create_precision_teaching(PrecisionTeachingCreate(
            title="力学新课", goal="诊断学生受力分析", content="受力分析", subject="物理",
        ), new_request)
        self.assertEqual(created["workspace_id"], new_id)
        self.assertEqual(len(bootstrap(new_request)["precision_teachings"]), 1)

        cross_subject = create_precision_teaching(PrecisionTeachingCreate(
            title="跨学科项目", goal="诊断学生建模能力", content="建模", subject="数学",
        ), new_request)
        self.assertEqual(cross_subject["workspace_id"], new_id)
        self.assertEqual(cross_subject["subject"], "数学")
        self.assertEqual(len(bootstrap(new_request)["precision_teachings"]), 2)
        updated_classroom = update_classroom(new_classroom["id"], ClassroomUpdate(student_count=38, background="可分组讨论"), new_request)
        self.assertEqual(updated_classroom["student_count"], 38)
        self.assertEqual(updated_classroom["background"], "可分组讨论")

        another_school = Response()
        teacher_auth.login(
            self.identity(school_name="另一所学校", teacher_name="陈老师", subject="物理"),
            request("/api/teacher-auth/login", method="POST"), another_school,
        )
        other_cookie = another_school.headers["set-cookie"].split(";", 1)[0]
        other_id = teacher_auth.current_teacher(request("/api/bootstrap", cookie=other_cookie))["workspace_id"]
        other_request = request("/api/bootstrap", cookie=other_cookie)
        other_request.state.teacher_workspace_id = other_id
        self.assertEqual(bootstrap(other_request)["classrooms"], [])
        classroom = create_classroom(ClassroomCreate(name="七年级（1）班"), other_request)
        self.assertEqual(bootstrap(other_request)["classrooms"][0]["id"], classroom["id"])
        self.assertNotIn(classroom["id"], {item["id"] for item in bootstrap(original_request)["classrooms"]})
        with self.assertRaises(HTTPException) as other_teacher_class:
            update_classroom(new_classroom["id"], ClassroomUpdate(student_count=1), other_request)
        self.assertEqual(other_teacher_class.exception.status_code, 404)

        async def next_response(_):
            return Response(status_code=204)

        cross_space = asyncio.run(protect_teacher_api(
            request(f"/api/precision-teachings/{created['id']}/workspace", cookie=original_cookie), next_response,
        ))
        self.assertEqual(cross_space.status_code, 404)

    def test_teacher_api_gate_and_cross_site_write(self):
        async def next_response(_):
            return Response(status_code=204)

        blocked = asyncio.run(protect_teacher_api(request("/api/bootstrap"), next_response))
        student = asyncio.run(protect_teacher_api(request("/api/student/login", method="POST"), next_response))
        options = asyncio.run(protect_teacher_api(request("/api/teacher-auth/login-options"), next_response))
        self.assertEqual(blocked.status_code, 401)
        self.assertEqual(student.status_code, 204)
        self.assertEqual(options.status_code, 204)

        login_response = Response()
        teacher_auth.login(self.identity(), request("/api/teacher-auth/login", method="POST"), login_response)
        cookie = login_response.headers["set-cookie"].split(";", 1)[0]
        allowed = asyncio.run(protect_teacher_api(request("/api/bootstrap", cookie=cookie), next_response))
        cross_site = asyncio.run(protect_teacher_api(
            request("/api/teacher-profile", method="PUT", cookie=cookie, origin="https://example.org"), next_response,
        ))
        self.assertEqual(allowed.status_code, 204)
        self.assertEqual(cross_site.status_code, 403)

    def test_published_classrooms_are_explicit_and_scoped_to_teacher(self):
        login_response = Response()
        teacher_auth.login(self.identity(), request("/api/teacher-auth/login", method="POST"), login_response)
        cookie = login_response.headers["set-cookie"].split(";", 1)[0]
        teacher_id = teacher_auth.current_teacher(request("/api/bootstrap", cookie=cookie))["workspace_id"]
        teacher_request = request("/api/bootstrap", cookie=cookie)
        teacher_request.state.teacher_workspace_id = teacher_id
        classes = bootstrap(teacher_request)["classrooms"]
        by_name = {item["name"]: item["id"] for item in classes}
        diagnosis = database_module.fetch_one("SELECT * FROM diagnosis_tasks WHERE teaching_id = 'pt-basic-inequality'")
        with database_module.database() as connection:
            connection.execute(
                """INSERT INTO diagnosis_rubrics
                   (teaching_id, skill_key, skill_version, provider, status, input_hash,
                    generated_json, current_json, generated_at, confirmed_at, updated_at)
                   VALUES ('pt-basic-inequality', 'solo_task_rubric_builder', '1', 'test', 'confirmed',
                           'test', '{}', '{}', ?, ?, ?)""",
                (database_module.utc_now(), database_module.utc_now(), database_module.utc_now()),
            )
        selected = by_name["高一（5）班"]
        payload = DiagnosisTaskUpdate(
            diagnosis_type=diagnosis["diagnosis_type"], goal=diagnosis["goal"],
            task_text=diagnosis["task_text"], ai_role=diagnosis["ai_role"],
            duration_minutes=diagnosis["duration_minutes"], status="published",
            classroom_ids=[selected],
        )
        result = update_diagnosis("pt-basic-inequality", payload)
        self.assertEqual(result["diagnosis"]["classroom_ids"], [selected])
        published = {item["name"] for school in student_api.login_options() for item in school["classes"]}
        self.assertIn("高一（5）班", published)
        self.assertNotIn("高一（3）班", published)
        with self.assertRaises(HTTPException) as unpublished_class:
            student_api.login_student(StudentLogin(school_name="杭州市求知中学", class_name="高一（3）班", name="未发布班级学生"))
        self.assertEqual(unpublished_class.exception.status_code, 404)
        self.assertEqual(workspace_for("pt-basic-inequality")["diagnosis"]["classroom_ids"], [selected])
        assignments = database_module.fetch_all(
            "SELECT classroom_id, is_published FROM diagnosis_assignments WHERE teaching_id = 'pt-basic-inequality'"
        )
        self.assertEqual({row["classroom_id"] for row in assignments if row["is_published"]}, {selected})

        other_response = Response()
        teacher_auth.login(self.identity(teacher_name="新老师"), request("/api/teacher-auth/login", method="POST"), other_response)
        other_cookie = other_response.headers["set-cookie"].split(";", 1)[0]
        other_id = teacher_auth.current_teacher(request("/api/bootstrap", cookie=other_cookie))["workspace_id"]
        other_request = request("/api/bootstrap", cookie=other_cookie)
        other_request.state.teacher_workspace_id = other_id
        own_class = create_classroom(ClassroomCreate(name="高一（8）班"), other_request)
        with self.assertRaises(HTTPException) as invalid:
            update_diagnosis("pt-basic-inequality", payload.model_copy(update={"classroom_ids": [own_class["id"]]}))
        self.assertEqual(invalid.exception.status_code, 422)
