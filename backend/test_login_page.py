from pathlib import Path
import unittest


FRONTEND = Path(__file__).resolve().parents[1] / 'frontend'


class LoginPageTest(unittest.TestCase):
    def test_teacher_login_has_no_admin_link_or_account_description(self):
        html = (FRONTEND / 'index.html').read_text(encoding='utf-8')
        self.assertNotIn('teacherAuthDescription', html)
        self.assertNotIn('管理员入口', html)
        self.assertNotIn('href="/admin/"', html)
        for field in ('teacherAuthForm', 'teacherAuthUsername', 'teacherAuthPassword'):
            self.assertIn(field, html)

    def test_admin_login_remains_separate(self):
        html = (FRONTEND / 'admin/index.html').read_text(encoding='utf-8')
        self.assertIn('type="password"', html)
        self.assertIn('admin.js', html)
