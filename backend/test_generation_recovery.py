"""Isolated class-report queue/restart regressions; never call a real model."""

from dataclasses import replace
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from unittest import TestCase
from unittest.mock import patch

from fastapi import BackgroundTasks

from backend.app import database as db
from backend.app.services import class_report as reports


class ClassReportRecoveryTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        settings = replace(db.settings, database_path=Path(self.temp.name) / "recovery.db", seed_demo_data=False)
        for module in (db, reports):
            patcher = patch.object(module, "settings", settings)
            patcher.start()
            self.addCleanup(patcher.stop)
        db.initialize_database()
        now = db.utc_now()
        with db.database() as connection:
            connection.execute("INSERT INTO precision_teachings (id,title,goal,subject,grade,created_at,updated_at) VALUES ('pt','模拟主题','模拟目标','数学','高一',?,?)", (now, now))
            connection.execute("INSERT INTO classrooms (id,name,created_at,updated_at) VALUES ('class','模拟班',?,?)", (now, now))
        self.source = "source-a"
        self.input_patch = patch.object(reports, "class_report_input", side_effect=lambda *_: ({"source": self.source}, self.source, {}))
        self.input_patch.start()
        self.addCleanup(self.input_patch.stop)
        self.patches = [
            patch("backend.app.services.class_teaching_suggestions.latest_suggestions", return_value=None),
            patch("jsonschema.validate"),
            patch.object(reports, "deterministic_distribution", return_value={"total_students": 1}),
            patch.object(reports, "validate_class_result"),
            patch.object(reports, "render_class_markdown", side_effect=lambda result: result["marker"]),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.key = reports._task_key("pt", "class")
        self.addCleanup(lambda: reports._report_tasks.pop(self.key, None))
        self.addCleanup(lambda: reports._running_tasks.pop(self.key, None))

    def seed_report(self, status="running"):
        skill = reports.load_skill("solo_class_diagnosis_intervention")
        with db.database() as connection:
            connection.execute(
                """INSERT INTO class_diagnosis_reports
                   (teaching_id,classroom_id,skill_key,skill_version,provider,status,source_hash,
                    generated_json,report_text,generated_at,updated_at)
                   VALUES ('pt','class',?,?,'mock',?,'source-a','{"old":true}','原报告','1999-01-01','1999-01-01T00:00:00+00:00')""",
                (skill.key, skill.version, status),
            )

    def queue(self, *, force=False):
        background = BackgroundTasks()
        result = reports.queue_class_report("pt", "class", background, force=force)
        return result, background

    def run_task(self, background):
        task = background.tasks[0]
        task.func(*task.args, **task.kwargs)

    def model_result(self, marker):
        return {"marker": marker, "class_summary": {}}, None

    def start_task(self, background):
        thread = Thread(target=self.run_task, args=(background,))
        thread.start()
        self.addCleanup(lambda: thread.join(timeout=5))
        return thread

    def row(self):
        return db.fetch_one("SELECT * FROM class_diagnosis_reports WHERE teaching_id='pt'")

    def test_restart_marks_queued_and_running_failed_and_preserves_saved_body(self):
        for state in ("queued", "running"):
            with self.subTest(state=state):
                with db.database() as connection:
                    connection.execute("DELETE FROM class_diagnosis_reports")
                    connection.execute("DELETE FROM ai_jobs")
                self.seed_report(state)
                with db.database() as connection:
                    connection.execute("INSERT INTO ai_jobs (id,skill_key,skill_version,provider,status,input_json,created_at) VALUES ('orphan','solo_class_diagnosis_intervention','1','mock','running','{}','1999-01-01')")
                    connection.execute("INSERT INTO ai_jobs (id,skill_key,skill_version,provider,status,input_json,created_at) VALUES ('other','other_skill','1','mock','running','{}','1999-01-01')")
                self.assertEqual(reports.recover_interrupted_class_reports(), 1)
                row = self.row()
                self.assertEqual(row["status"], "failed")
                self.assertIn("重新生成", row["error"])
                self.assertEqual((row["generated_json"], row["report_text"], row["generated_at"]), ('{"old":true}', "原报告", "1999-01-01"))
                self.assertEqual(db.fetch_one("SELECT status FROM ai_jobs WHERE id='orphan'")["status"], "failed")
                self.assertEqual(db.fetch_one("SELECT status FROM ai_jobs WHERE id='other'")["status"], "running")
                self.assertEqual(reports.recover_interrupted_class_reports(), 0)

    def test_orphan_is_failed_until_teacher_explicitly_retries(self):
        self.seed_report()
        row, background = self.queue()
        self.assertEqual(row["status"], "failed")
        self.assertFalse(background.tasks)
        retried, background = self.queue(force=True)
        self.assertEqual(retried["status"], "queued")
        self.assertEqual(retried["report_text"], "原报告")
        self.assertEqual(len(background.tasks), 1)

    def test_old_live_queue_is_not_expired_or_duplicated_even_on_force(self):
        _, background = self.queue()
        task = reports._report_tasks[self.key]
        task.stamp = "1999-01-01T00:00:00+00:00"
        with db.database() as connection:
            connection.execute("UPDATE class_diagnosis_reports SET updated_at=?", (task.stamp,))
        self.assertEqual(reports.recover_interrupted_class_reports(), 0)
        for force in (False, True):
            row, duplicate = self.queue(force=force)
            self.assertEqual(row["status"], "queued")
            self.assertFalse(duplicate.tasks)
        self.assertEqual(len(background.tasks), 1)

    def test_live_long_model_call_survives_recovery_and_duplicate_delivery(self):
        entered, release = Event(), Event()
        self.addCleanup(release.set)
        def model(*_):
            entered.set()
            self.assertTrue(release.wait(5))
            return self.model_result("完成")
        _, background = self.queue()
        with patch.object(reports, "run_class_diagnosis_report", side_effect=model) as mocked:
            thread = self.start_task(background)
            self.assertTrue(entered.wait(5))
            reports._report_tasks[self.key].queued_at -= 10000
            self.assertEqual(reports.recover_interrupted_class_reports(), 0)
            row, duplicate = self.queue(force=True)
            self.assertEqual(row["status"], "running")
            self.assertFalse(duplicate.tasks)
            self.run_task(background)
            self.assertEqual(mocked.call_count, 1)
            release.set()
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(self.row()["status"], "ready")

    def test_lost_unstarted_callback_becomes_retryable_without_model_call(self):
        _, lost = self.queue()
        reports._report_tasks[self.key].queued_at -= 121
        row, background = self.queue()
        self.assertEqual(row["status"], "failed")
        self.assertFalse(background.tasks)
        with patch.object(reports, "run_class_diagnosis_report") as model:
            self.run_task(lost)
            model.assert_not_called()
        retried, background = self.queue(force=True)
        self.assertEqual(retried["status"], "queued")
        self.assertEqual(len(background.tasks), 1)

    def test_initialization_failure_releases_owner_and_marks_failed(self):
        _, background = self.queue()
        with patch.object(reports, "load_skill", side_effect=RuntimeError("规则文件不可用")):
            self.run_task(background)
        self.assertEqual(self.row()["status"], "failed")
        self.assertEqual(self.row()["error"], "规则文件不可用")
        self.assertNotIn(self.key, reports._report_tasks)
        self.assertEqual(self.queue(force=True)[0]["status"], "queued")

    def test_input_precheck_exception_can_be_forced_and_completed(self):
        _, background = self.queue()
        with patch.object(reports, "class_report_input", side_effect=RuntimeError("读取输入失败")):
            self.run_task(background)
        self.assertEqual(self.row()["status"], "failed")
        self.assertNotIn(self.key, reports._report_tasks)
        _, retried = self.queue(force=True)
        with patch.object(reports, "run_class_diagnosis_report", return_value=self.model_result("重试成功")):
            self.run_task(retried)
        self.assertEqual((self.row()["status"], self.row()["report_text"]), ("ready", "重试成功"))

    def test_claim_exception_releases_owner_and_can_complete_on_retry(self):
        _, background = self.queue()
        calls = []
        @contextmanager
        def claim_fails_once():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("写入任务失败")
            with db.database() as connection:
                yield connection
        with patch.object(reports, "database", side_effect=claim_fails_once):
            self.run_task(background)
        self.assertEqual(self.row()["status"], "failed")
        self.assertNotIn(self.key, reports._report_tasks)
        _, retried = self.queue(force=True)
        with patch.object(reports, "run_class_diagnosis_report", return_value=self.model_result("写入已恢复")):
            self.run_task(retried)
        self.assertEqual((self.row()["status"], self.row()["report_text"]), ("ready", "写入已恢复"))

    def test_parallel_queue_requests_schedule_exactly_one_generation(self):
        backgrounds = []
        def queue():
            backgrounds.append(self.queue(force=True)[1])
        threads = [Thread(target=queue) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(sum(len(task.tasks) for task in backgrounds), 1)

    def test_changed_source_waits_for_old_worker_and_old_result_never_publishes(self):
        first_entered, first_release, next_entered, next_release = Event(), Event(), Event(), Event()
        self.addCleanup(first_release.set)
        self.addCleanup(next_release.set)
        calls = []
        def model(_, payload, __):
            calls.append(payload["source"])
            if len(calls) == 1:
                first_entered.set()
                self.assertTrue(first_release.wait(5))
            else:
                next_entered.set()
                self.assertTrue(next_release.wait(5))
            return self.model_result(payload["source"])
        self.seed_report("ready")
        _, first = self.queue(force=True)
        with patch.object(reports, "run_class_diagnosis_report", side_effect=model):
            thread = self.start_task(first)
            self.assertTrue(first_entered.wait(5))
            self.source = "source-b"
            _, second = self.queue()
            self.assertEqual(reports.recover_interrupted_class_reports(), 0)
            self.assertEqual(db.fetch_one("SELECT status FROM ai_jobs")["status"], "running")
            self.run_task(second)
            self.assertEqual(calls, ["source-a"])
            first_release.set()
            self.assertTrue(next_entered.wait(5))
            self.assertEqual(self.row()["report_text"], "原报告")
            next_release.set()
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(calls, ["source-a", "source-b"])
        self.assertEqual((self.row()["status"], self.row()["report_text"]), ("ready", "source-b"))

    def test_superseded_same_source_token_cannot_publish_after_source_changes_back(self):
        entered, release, next_entered, next_release = Event(), Event(), Event(), Event()
        self.addCleanup(release.set)
        self.addCleanup(next_release.set)
        calls = []
        def model(*_):
            calls.append(len(calls) + 1)
            if len(calls) == 1:
                entered.set()
                self.assertTrue(release.wait(5))
            else:
                next_entered.set()
                self.assertTrue(next_release.wait(5))
            return self.model_result(str(len(calls)))
        self.seed_report("ready")
        _, first = self.queue(force=True)
        with patch.object(reports, "run_class_diagnosis_report", side_effect=model):
            thread = self.start_task(first)
            self.assertTrue(entered.wait(5))
            self.source = "source-b"
            _, obsolete = self.queue()
            self.source = "source-a"
            _, newest = self.queue()
            release.set()
            self.assertTrue(next_entered.wait(5))
            self.assertEqual(self.row()["report_text"], "原报告")
            next_release.set()
            thread.join(5)
            self.run_task(obsolete)
            self.run_task(newest)
        self.assertEqual(calls, [1, 2])
        self.assertEqual(self.row()["report_text"], "2")

    def test_obsolete_failure_does_not_fail_newer_generation(self):
        entered, release = Event(), Event()
        self.addCleanup(release.set)
        def model(_, payload, __):
            if payload["source"] == "source-a":
                entered.set()
                self.assertTrue(release.wait(5))
                raise RuntimeError("旧任务失败")
            return self.model_result("新结果")
        _, first = self.queue()
        with patch.object(reports, "run_class_diagnosis_report", side_effect=model):
            thread = self.start_task(first)
            self.assertTrue(entered.wait(5))
            self.source = "source-b"
            self.queue()
            release.set()
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual((self.row()["status"], self.row()["report_text"], self.row()["error"]), ("ready", "新结果", None))

    def test_unqueued_source_change_fails_instead_of_leaving_running(self):
        def model(*_):
            self.source = "source-b"
            return self.model_result("已失效结果")
        _, background = self.queue()
        with patch.object(reports, "run_class_diagnosis_report", side_effect=model):
            self.run_task(background)
        self.assertEqual(self.row()["status"], "failed")
        self.assertIn("输入已变化", self.row()["error"])
        self.assertIsNone(self.row()["generated_json"])

