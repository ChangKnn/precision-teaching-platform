from html.parser import HTMLParser
from pathlib import Path
import unittest

FRONTEND = Path(__file__).resolve().parents[1] / 'frontend'


class FilingParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.footer = False
        self.links = []
        self.images = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'footer' and attrs.get('class') == 'site-filing':
            self.footer = True
        if self.footer and tag == 'a':
            self.links.append(attrs)
        if self.footer and tag == 'img':
            self.images.append(attrs)

    def handle_endtag(self, tag):
        if tag == 'footer':
            self.footer = False


class SiteFilingTest(unittest.TestCase):
    def test_filing_visible_on_both_original_platform_entry_pages(self):
        for page in ('index.html', 'student/index.html', 'admin/index.html'):
            with self.subTest(page=page):
                html = (FRONTEND / page).read_text(encoding='utf-8')
                self.assertIn('京ICP备2026068374号-1', html)
                self.assertIn('京公网安备11010802050729号', html)
                self.assertIn('/filing.css?v=20261018', html)
                parser = FilingParser()
                parser.feed(html)
                self.assertEqual(len(parser.links), 2)
                self.assertEqual(parser.links[0]['href'], 'https://beian.miit.gov.cn/')
                self.assertEqual(parser.links[1]['href'], 'https://beian.mps.gov.cn/#/query/webSearch?code=11010802050729')
                for link in parser.links:
                    self.assertEqual(link['target'], '_blank')
                    self.assertIn('noopener', link['rel'])
                self.assertEqual(len(parser.images), 1)
                self.assertTrue((FRONTEND / parser.images[0]['src'].lstrip('/')).is_file())
                self.assertLess(html.index('<img src="/assets/beian.png"'), html.index('京公网安备11010802050729号'))

    def test_teacher_and_student_functionality_preserved(self):
        self.assertIn('teacherAuthForm', (FRONTEND / 'index.html').read_text(encoding='utf-8'))
        self.assertIn('student.js', (FRONTEND / 'student/index.html').read_text(encoding='utf-8'))
