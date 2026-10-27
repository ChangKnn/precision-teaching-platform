"""Class names and reversible teacher removal preserve student identities and evidence."""
from types import SimpleNamespace
from unittest import TestCase

from fastapi import HTTPException
from backend import test_task_version_runtime as runtime_tests
from backend.app import database as db,main,student_api
from backend.app.classroom_management import delete_classroom,restore_classroom,deleted_classrooms
from backend.app.schemas import ClassroomUpdate,StudentLogin


class ClassroomManagementTest(TestCase):
    def setUp(self):
        self.case=runtime_tests.TaskVersionRuntimeTest('test_unchanged_publish_does_not_create_spurious_versions')
        self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.request=SimpleNamespace(state=SimpleNamespace(teacher_workspace_id='ws'))
        with db.database() as connection:
            connection.execute("INSERT INTO classroom_schools VALUES ('class','测试学校',?)",(db.utc_now(),))

    def test_rename_keeps_student_identity_login_and_all_previous_answers(self):
        sid=self.case.start()['session']['id'];self.case.send(sid)
        before=db.fetch_all('SELECT * FROM student_messages ORDER BY id')
        main.update_classroom('class',ClassroomUpdate(name='研讨班新名称',student_count=20,background='新背景'),self.request)
        result=student_api.login_student(StudentLogin(school_name='测试学校',class_name='研讨班新名称',name='模拟学生'))
        self.assertEqual(result['student']['id'],'stu')
        self.assertEqual(db.fetch_one('SELECT COUNT(*) n FROM students')['n'],1)
        self.assertEqual(db.fetch_all('SELECT * FROM student_messages ORDER BY id'),before)
        self.assertEqual(db.fetch_one("SELECT class_name FROM students WHERE id='stu'")['class_name'],'研讨班新名称')

    def test_blank_and_duplicate_names_are_rejected_without_changing_class(self):
        now=db.utc_now()
        with db.database() as connection:
            connection.execute("INSERT INTO classrooms (id,name,created_at,updated_at) VALUES ('class2','已存在班级',?,?)",(now,now))
            connection.execute("INSERT INTO classroom_schools VALUES ('class2','测试学校',?)",(now,))
        for name,code in [('  ',422),('已存在班级',409)]:
            with self.subTest(name=name),self.assertRaises(HTTPException) as error:
                main.update_classroom('class',ClassroomUpdate(name=name,student_count=1),self.request)
            self.assertEqual(error.exception.status_code,code)
        self.assertEqual(db.fetch_one("SELECT name FROM classrooms WHERE id='class'")['name'],'测试班')

    def test_archive_and_restore_preserve_student_sessions_versions_and_assignments(self):
        sid=self.case.start()['session']['id'];self.case.send(sid)
        tables=('students','student_messages','student_task_sessions','diagnosis_task_versions','diagnosis_tasks')
        before={table:db.fetch_all(f'SELECT * FROM {table} ORDER BY rowid') for table in tables}
        self.assertTrue(delete_classroom('class',self.request)['deleted'])
        self.assertTrue(delete_classroom('class',self.request)['deleted'])
        self.assertEqual(student_api.login_options(),[])
        self.assertEqual(main.bootstrap(self.request)['classrooms'],[])
        self.assertEqual(deleted_classrooms('ws')[0]['id'],'class')
        self.assertIsNone(db.fetch_one("SELECT 1 FROM classroom_workspaces WHERE classroom_id='class' AND workspace_id='ws'"))
        self.assertEqual(db.fetch_one("SELECT is_published FROM diagnosis_assignments WHERE teaching_id='pt'")['is_published'],0)
        for table in tables:self.assertEqual(db.fetch_all(f'SELECT * FROM {table} ORDER BY rowid'),before[table])
        self.assertTrue(restore_classroom('class',self.request)['restored'])
        self.assertEqual(deleted_classrooms('ws'),[])
        self.assertEqual(main.bootstrap(self.request)['classrooms'][0]['id'],'class')
        self.assertEqual(student_api.login_options()[0]['classes'][0]['name'],'测试班')
        for table in tables:self.assertEqual(db.fetch_all(f'SELECT * FROM {table} ORDER BY rowid'),before[table])

    def test_delete_only_removes_current_teachers_links_and_task_access(self):
        now=db.utc_now()
        with db.database() as connection:
            connection.execute("INSERT INTO teacher_workspaces (id,school_name,subject,teacher_name,identity_key,created_at,updated_at) VALUES ('other','测试学校','语文','其他教师','other',?,?)",(now,now))
            connection.execute("INSERT INTO classroom_workspaces VALUES ('class','other',?)",(now,))
            connection.execute("INSERT INTO precision_teachings (id,workspace_id,title,goal,content,subject,grade,created_at,updated_at) VALUES ('other-task','other','其他教师任务','说明理由','语文学习','语文','高一',?,?)",(now,now))
            connection.execute("INSERT INTO diagnosis_tasks (teaching_id,status,updated_at) VALUES ('other-task','published',?)",(now,))
            connection.execute("INSERT INTO diagnosis_assignments VALUES ('other-task','class',?,1)",(now,))
        delete_classroom('class',self.request)
        self.assertIsNotNone(db.fetch_one("SELECT 1 FROM classroom_workspaces WHERE workspace_id='other'"))
        self.assertEqual(db.fetch_one("SELECT is_published FROM diagnosis_assignments WHERE teaching_id='other-task'")['is_published'],1)
        self.assertTrue(student_api.login_options())

    def test_cross_teacher_mutations_are_denied_and_deleted_classes_cannot_be_edited(self):
        other=SimpleNamespace(state=SimpleNamespace(teacher_workspace_id='not-owner'))
        for operation in (lambda:main.update_classroom('class',ClassroomUpdate(name='越权',student_count=1),other),lambda:delete_classroom('class',other),lambda:restore_classroom('class',other)):
            with self.assertRaises(HTTPException) as error:operation()
            self.assertEqual(error.exception.status_code,404)
        delete_classroom('class',self.request)
        with self.assertRaises(HTTPException):main.update_classroom('class',ClassroomUpdate(student_count=1),self.request)

    def test_old_clients_can_still_update_only_count_and_background(self):
        updated=main.update_classroom('class',ClassroomUpdate(student_count=30,background='教师补充背景'),self.request)
        self.assertEqual(updated['name'],'测试班')
        self.assertEqual(updated['student_count'],30)
