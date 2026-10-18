from html.parser import HTMLParser
from pathlib import Path
import unittest

SITE = Path(__file__).resolve().parents[1] / 'notes-site'


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.images, self.ids, self.tags = [], [], set(), []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append(tag)
        if 'id' in attrs:
            self.ids.add(attrs['id'])
        if tag == 'a':
            self.links.append(attrs)
        if tag == 'img':
            self.images.append(attrs)


class NotesSiteTest(unittest.TestCase):
    def setUp(self):
        self.html = (SITE / 'index.html').read_text(encoding='utf-8')
        self.page = Page()
        self.page.feed(self.html)

    def test_official_filing_links_and_service_number(self):
        self.assertIn('京ICP备2026068374号-1', self.html)
        self.assertIn('京公网安备11010802050729号', self.html)
        urls = {a['href'] for a in self.page.links}
        self.assertIn('https://beian.miit.gov.cn/', urls)
        self.assertIn('https://beian.mps.gov.cn/#/query/webSearch?code=11010802050729', urls)
        for a in self.page.links:
            if a.get('target') == '_blank':
                self.assertIn('noopener', a['rel'])
                self.assertIn('noreferrer', a['rel'])

    def test_local_images_and_anchor_targets_exist(self):
        for image in self.page.images:
            self.assertTrue((SITE / image['src']).is_file())
            self.assertTrue(image['alt'])
        for a in self.page.links:
            if a['href'].startswith('#'):
                self.assertIn(a['href'][1:], self.page.ids)

    def test_police_icon_precedes_number(self):
        self.assertLess(self.html.index('<img src="assets/beian.png"'), self.html.index('京公网安备11010802050729号'))

    def test_standalone_content_site_has_no_old_platform_entry(self):
        self.assertNotIn('script', self.page.tags)
        self.assertNotIn('form', self.page.tags)
        self.assertNotIn('/student/', self.html)
        self.assertNotIn('/api/', self.html)
