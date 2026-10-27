from html.parser import HTMLParser
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
PORTAL = ROOT / 'portal'


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.links = []
        self.link_text = []
        self.resources = []
        self.images = []
        self.tags = set()
        self.text = []
        self._inside_link = False
        self._current_link_text = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.tags.add(tag)
        if attributes.get('id'):
            self.ids.add(attributes['id'])
        if tag == 'a':
            self.links.append(attributes.get('href', ''))
            self.link_text.append('')
            self._inside_link = True
            self._current_link_text = []
        if tag in ('script', 'link'):
            resource = attributes.get('src') or attributes.get('href', '')
            if resource:
                self.resources.append(resource)
        if tag == 'img':
            self.resources.append(attributes.get('src', ''))
            self.images.append(attributes)

    def handle_data(self, data):
        self.text.append(data)
        if self._inside_link:
            self._current_link_text.append(data)

    def handle_endtag(self, tag):
        if tag == 'a' and self._inside_link:
            self.link_text[-1] = ''.join(self._current_link_text)
            self._inside_link = False
            self._current_link_text = []


class PortalStaticTests(unittest.TestCase):
    def test_portal_has_semantic_sections_and_valid_local_navigation(self):
        page_path = PORTAL / 'index.html'
        self.assertTrue(page_path.is_file(), 'portal/index.html must exist')
        markup = page_path.read_text(encoding='utf-8')
        parser = PageParser()
        parser.feed(markup)

        self.assertIn('zh-CN', markup)
        self.assertIn('main', parser.tags)
        self.assertTrue({
            'top', 'features', 'formats', 'editing',
            'reading', 'workflow', 'limits',
        }.issubset(parser.ids))
        self.assertTrue((PORTAL / 'assets' / 'nvlogo.png').is_file())
        self.assertEqual(len(parser.images), 2)
        self.assertEqual(
            [image.get('src') for image in parser.images],
            ['assets/nvlogo.png', 'assets/nvlogo.png'],
        )
        self.assertTrue(all(image.get('alt') == 'Notepad View 标志'
                            for image in parser.images))
        self.assertIn('浏览功能亮点', ''.join(parser.text))
        for href in parser.links:
            if href.startswith('#'):
                self.assertIn(href[1:], parser.ids, f'broken in-page link: {href}')
        for resource in parser.resources:
            self.assertFalse(
                resource.startswith(('http:', 'https:', '//')),
                f'remote resource is not allowed: {resource}',
            )
            self.assertTrue((PORTAL / resource).is_file(), f'missing local resource: {resource}')

        css = (PORTAL / 'styles.css').read_text(encoding='utf-8')
        script = (PORTAL / 'script.js').read_text(encoding='utf-8')
        self.assertRegex(css, r'@media[^{]*max-width')
        self.assertIn('prefers-reduced-motion', css)
        self.assertIn(':focus-visible', css)
        self.assertIn('aria-expanded', markup)
        self.assertIn('aria-expanded', script)
        self.assertIn('focus({ preventScroll: true })', script)
        self.assertIn('requestAnimationFrame', script)
        for asset in (css, script):
            self.assertNotRegex(asset, re.compile(r'https?://|//[^/]', re.IGNORECASE))
        for api in ('fetch(', 'XMLHttpRequest', 'WebSocket('):
            self.assertNotIn(api, script)

    def test_portal_uses_logo_palette_instead_of_previous_cinnabar_theme(self):
        css = (PORTAL / 'styles.css').read_text(encoding='utf-8')
        self.assertIn('--navy: #003080', css)
        self.assertIn('--blue: #2060B0', css)
        self.assertIn('--cyan: #30FFFF', css)
        self.assertRegex(css, r'--violet:\s*#[0-9a-fA-F]{6}')
        for old_color in ('--red', '--red-bright', '#c64d35', '#e26b4e'):
            self.assertNotIn(old_color, css)
        self.assertRegex(css, r'\.brand-logo\s*\{[^}]*background:\s*#fff')

    def test_portal_copy_is_readable_and_logo_is_prominent(self):
        css = (PORTAL / 'styles.css').read_text(encoding='utf-8')
        rules = re.findall(r'([^{}]+)\{([^{}]*)\}', css)
        self.assertIn('--minimum-copy-size: 12px', css)
        readable_rule = next(
            (selector for selector, declarations in rules
             if 'font-size: var(--minimum-copy-size)' in declarations),
            '',
        )
        readable_selectors = {item.strip() for item in readable_rule.split(',')}
        self.assertTrue({
            '.brand-note', '.eyebrow', '.section-kicker', '.mini-label',
            '.text-link', '.light-link', '.hero-footnote', '.art-caption',
            '.floating-note', '.hero-index', '.tile-number', '.feature-tile p',
            '.feature-tile > a', '.format-mode', '.format-card p',
            '.format-chips span', '.format-footnote', '.check-list li',
            '.check-list li span', '.panel-label', '.reading-panel > p',
            '.page-tag', '.panel-note', '.runtime-note p', '.runtime-note > a',
            '.step-count', '.workflow-steps p', '.workflow-ribbon',
            '.closing-section .eyebrow', '.limits-copy > p', '.limit-items p',
            '.limit-items p span', '.version-note', '.footer-brand',
            '.site-footer > p', '.back-top', '.site-footer small',
            '.editing-copy > p:not(.mini-label)',
        }.issubset(readable_selectors))

        self.assertRegex(css, r'\.brand-logo\s*\{[^}]*width:\s*48px')
        self.assertRegex(css, r'\.brand-logo\s*\{[^}]*height:\s*48px')
        self.assertRegex(css, r'\.footer-brand \.brand-logo\s*\{[^}]*width:\s*40px')

    def test_portal_accurately_describes_product_capabilities(self):
        self.assertTrue((PORTAL / 'index.html').is_file(), 'portal/index.html must exist')
        markup = (PORTAL / 'index.html').read_text(encoding='utf-8')
        parser = PageParser()
        parser.feed(markup)
        copy = ''.join(parser.text).casefold()

        for feature in ('markdown', '实时预览', '编码', '工作区', 'pdf',
                        'docx', 'xlsx', 'epub', 'calibre', 'drm', '只读'):
            self.assertIn(feature, copy, f'missing product capability or limitation: {feature}')
        self.assertNotRegex(markup, re.compile(r'https?://|//[^/]', re.IGNORECASE))
        for href, label in zip(parser.links, parser.link_text):
            self.assertNotRegex(href, re.compile(r'download|setup|installer', re.IGNORECASE))
            self.assertNotIn('下载', label)


if __name__ == '__main__':
    unittest.main()
