import asyncio
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException, Response
from pydantic import ValidationError

from backend.app import admin_api as admin, database as db, teacher_auth as auth
from backend.app.main import protect_teacher_api
from backend.app.manage_accounts import bootstrap_admin
from backend.test_teacher_auth import request


class AccountSecurityTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        settings = patch.object(db, 'settings', SimpleNamespace(database_path=self.folder / 'test.db', seed_demo_data=False))
        settings.start()
        self.addCleanup(settings.stop)
        db.initialize_database()
        self.password = 'Original-Password-12345'
        with db.database() as connection:
            now = db.utc_now()
            connection.execute("""INSERT INTO user_accounts
                (id,username,role,password_hash,must_change_password,created_at,updated_at)
                VALUES ('admin-1','admin','admin',?,0,?,?)""", (auth.hash_password(self.password), now, now))
        self.admin_request = self.login('admin', 'admin')

    def login(self, username, role='teacher', password=None):
        response = Response()
        login = auth.admin_login if role == 'admin' else auth.login
        login(auth.Credentials(username=username, password=password or self.password),
              request('/api/' + ('admin-auth' if role == 'admin' else 'teacher-auth') + '/login', method='POST'), response)
        cookie = response.headers['set-cookie'].split(';', 1)[0]
        return request('/api/admin/teachers' if role == 'admin' else '/api/bootstrap', cookie=cookie)

    def create(self, username='teacher1', workspace=None):
        return admin.create_teacher(admin.TeacherCreate(username=username, password=self.password,
            teacher_name='测试教师', school_name='测试学校', subject='数学', workspace_id=workspace), self.admin_request)

    def gate(self, path, req=None, method='GET', origin=''):
        async def next_response(_):
            return Response(status_code=204)
        cookie = req.headers.get('cookie','') if req else ''
        return asyncio.run(protect_teacher_api(request(path, method=method, cookie=cookie, origin=origin), next_response))

    def ready_teacher(self):
        created = self.create()
        req = self.login('teacher1')
        response = Response()
        auth.change_password(auth.PasswordChange(current_password=self.password, new_password='Personal-Password-98765'), req, response)
        cookie = response.headers['set-cookie'].split(';',1)[0]
        return created, request('/api/bootstrap',cookie=cookie)

    def test_salted_password_hashes_and_constant_format(self):
        one, two = auth.hash_password(self.password), auth.hash_password(self.password)
        self.assertNotEqual(one, two)
        self.assertNotIn(self.password, one)
        self.assertTrue(auth.verify_password(self.password, one))
        self.assertFalse(auth.verify_password('wrong', one))
        self.assertFalse(auth.verify_password(self.password, 'bad-format'))

    def test_no_identity_only_login_or_role_injection(self):
        with self.assertRaises(ValidationError):
            auth.Credentials(school_name='学校', subject='数学', teacher_name='老师')
        with self.assertRaises(ValidationError):
            admin.TeacherCreate(username='evil',password=self.password,role='admin')
        self.assertIsNone(auth.current_teacher(request('/api/bootstrap',cookie='sijie_teacher_identity_session=old-token')))

    def test_unknown_login_cannot_create_account_or_workspace(self):
        before = db.fetch_one('SELECT COUNT(*) n FROM teacher_workspaces')['n']
        with self.assertRaises(HTTPException) as denied:
            self.login('not-created')
        self.assertEqual(denied.exception.status_code,401)
        self.assertEqual(db.fetch_one('SELECT COUNT(*) n FROM teacher_workspaces')['n'], before)

    def test_only_admin_can_create_and_manage_teachers(self):
        payload = admin.TeacherCreate(username='teacher1',password=self.password,school_name='学校',teacher_name='教师',subject='数学')
        with self.assertRaises(HTTPException) as denied:
            admin.create_teacher(payload,request('/api/admin/teachers'))
        self.assertEqual(denied.exception.status_code,401)
        _, teacher = self.ready_teacher()
        for path in ('/api/admin/teachers','/api/admin/teachings','/api/admin/overview'):
            self.assertEqual(self.gate(path,teacher).status_code,403)
        self.assertEqual(self.gate('/api/admin/teachers', self.admin_request).status_code,204)
        self.assertEqual(self.gate('/api/bootstrap',self.admin_request).status_code,403)
        with self.assertRaises(HTTPException):
            admin.list_teachers(teacher)

    def test_admin_created_teacher_can_use_assigned_password_without_initial_change(self):
        created = self.create()
        teacher = self.login('teacher1')
        self.assertFalse(auth.status(teacher,Response())['must_change_password'])
        self.assertEqual(self.gate('/api/bootstrap',teacher).status_code,204)
        self.assertEqual(self.gate('/api/teacher-auth/password',teacher,method='POST').status_code,204)
        stored = db.fetch_one('SELECT * FROM user_accounts WHERE id = ?', (created['id'],))
        self.assertEqual(stored['must_change_password'],0)
        self.assertNotEqual(stored['password_hash'],self.password)
        self.assertTrue(auth.verify_password(self.password,stored['password_hash']))

    def test_existing_initial_password_gate_and_required_change_remains(self):
        self.create()
        with db.database() as connection:
            connection.execute("UPDATE user_accounts SET must_change_password=1 WHERE username='teacher1'")
        teacher = self.login('teacher1')
        self.assertEqual(self.gate('/api/bootstrap',teacher).status_code,403)
        self.assertEqual(self.gate('/api/teacher-auth/password',teacher,method='POST').status_code,204)
        self.assertEqual(self.gate('/api/teacher-auth/logout',teacher,method='POST').status_code,204)
        self.assertTrue(auth.status(teacher,Response())['must_change_password'])

    def test_teacher_password_change_revokes_all_previous_sessions(self):
        self.create()
        first, second = self.login('teacher1'), self.login('teacher1')
        response = Response()
        auth.change_password(auth.PasswordChange(current_password=self.password,new_password='Personal-Password-98765'),first,response)
        self.assertIsNone(auth.current_account(first))
        self.assertIsNone(auth.current_account(second))
        current = request('/api/bootstrap',cookie=response.headers['set-cookie'].split(';',1)[0])
        self.assertEqual(self.gate('/api/bootstrap',current).status_code,204)
        self.assertFalse(auth.current_account(current)['must_change_password'])
        self.assertNotIn('password_hash',json.dumps(auth.status(current,Response())))

    def test_disable_and_reenable_do_not_revive_old_sessions(self):
        created, teacher = self.ready_teacher()
        admin.set_teacher_state(created['id'],admin.AccountState(enabled=False),self.admin_request)
        self.assertIsNone(auth.current_account(teacher))
        with self.assertRaises(HTTPException):
            self.login('teacher1',password='Personal-Password-98765')
        admin.set_teacher_state(created['id'],admin.AccountState(enabled=True),self.admin_request)
        self.assertIsNone(auth.current_account(teacher))
        self.assertIsNotNone(auth.current_account(self.login('teacher1',password='Personal-Password-98765')))

    def test_password_reset_revokes_old_access_and_requires_change(self):
        created, teacher = self.ready_teacher()
        admin.reset_teacher_password(created['id'],admin.PasswordReset(password='Reset-Password-012345'),self.admin_request)
        self.assertIsNone(auth.current_account(teacher))
        with self.assertRaises(HTTPException):
            self.login('teacher1',password='Personal-Password-98765')
        reset = self.login('teacher1',password='Reset-Password-012345')
        self.assertEqual(self.gate('/api/bootstrap',reset).status_code,403)
        audit = db.fetch_all('SELECT detail_json FROM audit_logs')
        self.assertNotIn('Reset-Password',str(audit))
        self.assertNotIn(self.password,str(audit))

    def test_stale_login_cannot_issue_session_after_reset(self):
        created = self.create()
        old = db.fetch_one('SELECT * FROM user_accounts WHERE id = ?', (created['id'],))
        old['account_id'] = old['id']
        admin.reset_teacher_password(created['id'],admin.PasswordReset(password='Reset-Password-012345'),self.admin_request)
        with self.assertRaises(HTTPException):
            auth._issue_session(Response(),request('/'),old)

    def test_bind_legacy_workspace_preserves_tasks_and_rejects_double_binding(self):
        with db.database() as connection:
            now = db.utc_now()
            connection.execute("""INSERT INTO teacher_workspaces (id,school_name,subject,teacher_name,identity_key,created_at,updated_at)
                VALUES ('legacy','学校','数学','旧教师','legacy-key',?,?)""",(now,now))
            connection.execute("""INSERT INTO precision_teachings(id,workspace_id,title,goal,content,subject,grade,created_at,updated_at)
                VALUES ('task-old','legacy','旧任务','旧目标','旧内容','数学','高一',?,?)""",(now,now))
        self.assertEqual(admin.list_teachers(self.admin_request)['unbound_workspaces'][0]['id'],'legacy')
        account = self.create(workspace='legacy')
        self.assertEqual(account['workspace_id'],'legacy')
        self.assertEqual(db.fetch_one("SELECT title FROM precision_teachings WHERE id='task-old'")['title'],'旧任务')
        with self.assertRaises(HTTPException) as duplicate:
            self.create(username='another',workspace='legacy')
        self.assertEqual(duplicate.exception.status_code,409)
        tasks = admin.list_teachings(self.admin_request,workspace_id='legacy')
        self.assertEqual(tasks['total'],1)
        detail = admin.teaching_detail('task-old',self.admin_request)
        self.assertEqual(detail['teaching']['goal'],'旧目标')
        self.assertEqual(detail['classrooms'],[])
        self.assertNotIn('password_hash',json.dumps(admin.list_teachers(self.admin_request)))

    def test_duplicate_username_transaction_does_not_leave_new_workspace(self):
        self.create()
        before = db.fetch_one('SELECT COUNT(*) n FROM teacher_workspaces')['n']
        with self.assertRaises(HTTPException):
            auth.provision_teacher('teacher1',self.password,'其他学校','语文','别的教师')
        self.assertEqual(db.fetch_one('SELECT COUNT(*) n FROM teacher_workspaces')['n'],before)

    def test_admin_login_and_teacher_login_are_separate(self):
        self.create()
        with self.assertRaises(HTTPException):
            self.login('teacher1','admin')
        with self.assertRaises(HTTPException):
            self.login('admin','teacher')

    def test_cross_site_writes_and_admin_login_rejected(self):
        for path in ('/api/admin/teachers','/api/admin-auth/login','/api/teacher-auth/login'):
            self.assertEqual(self.gate(path,self.admin_request,method='POST',origin='https://attacker.example').status_code,403)

    def test_login_attempts_throttled_before_hashing(self):
        with patch.object(auth,'verify_password',return_value=False) as verify:
            for _ in range(10):
                with self.assertRaises(HTTPException):
                    self.login('missing')
            with self.assertRaises(HTTPException) as blocked:
                self.login('missing')
            self.assertEqual(blocked.exception.status_code,429)
            self.assertEqual(verify.call_count,10)

    def test_https_cookie_and_session_expiry(self):
        response = Response()
        req = request('/api/admin-auth/login')
        req.scope['scheme'] = 'https'
        auth.admin_login(auth.Credentials(username='admin',password=self.password),req,response)
        self.assertIn('Secure',response.headers['set-cookie'])
        self.assertIn('HttpOnly',response.headers['set-cookie'])
        self.assertIn('SameSite=strict',response.headers['set-cookie'])
        cookie = response.headers['set-cookie'].split(';',1)[0]
        with db.database() as connection:
            connection.execute("UPDATE account_sessions SET expires_at='2000-01-01T00:00:00+00:00'")
        self.assertIsNone(auth.current_account(request('/',cookie=cookie)))

    def test_cli_bootstrap_does_not_overwrite_existing_admin_or_credentials(self):
        output = self.folder / 'initial-admin.txt'
        with self.assertRaises(RuntimeError):
            bootstrap_admin('second-admin',output)
        self.assertFalse(output.exists())
        self.assertEqual(db.fetch_one("SELECT COUNT(*) n FROM user_accounts WHERE role='admin'")['n'],1)
        with db.database() as connection:
            connection.execute('DELETE FROM account_sessions')
            connection.execute("DELETE FROM user_accounts WHERE role='admin'")
        bootstrap_admin('first-admin',output)
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(output.stat().st_mode),0o600)
        text = output.read_text(encoding='utf-8')
        self.assertIn('first-admin',text)
        self.assertEqual(db.fetch_one("SELECT must_change_password FROM user_accounts WHERE role='admin'")['must_change_password'],1)
        with self.assertRaises(FileExistsError):
            bootstrap_admin('third-admin',output)

    def test_admin_cannot_be_disabled_via_teacher_endpoint(self):
        with self.assertRaises(HTTPException) as invalid:
            admin.set_teacher_state('admin-1',admin.AccountState(enabled=False),self.admin_request)
        self.assertEqual(invalid.exception.status_code,404)
