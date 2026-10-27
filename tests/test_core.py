def test_unicode_document_roundtrip(tmp_path):
    from notepadview.document import read_document, save_document
    path = tmp_path / 'notes.md'
    save_document(path, '# \u4e2d\u6587\n\nhello\n')
    assert read_document(path) == '# \u4e2d\u6587\n\nhello\n'
    assert list(tmp_path.iterdir()) == [path]


def test_read_bom_and_windows_newlines(tmp_path):
    from notepadview.document import read_document
    path = tmp_path / 'bom.md'
    path.write_bytes(b'\xef\xbb\xbf# Hello\r\nWorld')
    assert read_document(path) == '# Hello\nWorld'


def test_markdown_tables_tasks_code_and_relative_image():
    from notepadview.rendering import render_html
    html = render_html('# Title\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n- [x] done\n\n```python\nprint(1)\n```\n\n![sample](images/demo.png)', 'light')
    assert '<h1 id="heading-0">Title</h1>' in html
    assert '<table>' in html
    assert 'type="checkbox"' in html
    assert 'highlight' in html
    assert html.count('<pre') == 1
    assert 'images/demo.png' in html


def test_untrusted_html_and_javascript_links_are_inert():
    from notepadview.rendering import render_html
    html = render_html('<script>alert(1)</script>\n\n[x](javascript:alert(1))', 'dark')
    assert '<script>' not in html
    assert 'href="javascript:' not in html
    assert 'Content-Security-Policy' in html


def test_markdown_heading_anchors_follow_source_lines():
    from notepadview.rendering import render_html
    source = '# Same\n\n~~~md\n# Not a heading\n~~~\n\nSame\n----\n\n  ## **中文**\n'
    html = render_html(source)
    assert '<h1 id="heading-0">Same</h1>' in html
    assert '<h2 id="heading-6">Same</h2>' in html
    assert '<h2 id="heading-9"><strong>中文</strong></h2>' in html
    assert 'id="heading-3"' not in html


def test_theme_changes_rendered_colors():
    from notepadview.rendering import render_html
    assert render_html('hello', 'light') != render_html('hello', 'dark')


def test_soft_theme_covers_markdown_plain_text_and_code_exports():
    from notepadview.rendering import render_html, render_plain_text, render_code_preview
    for html in (render_html('# Heading', 'soft'), render_plain_text('text', 'soft'),
                 render_code_preview('select 1', 'sql', 'soft')):
        assert 'background:#e8f1e8' in html
        assert '#283c32' in html
        assert 'Content-Security-Policy' in html


def test_soft_theme_comment_has_readable_contrast():
    from notepadview.syntax import style_for_theme
    from pygments.token import Comment
    color = style_for_theme('soft').style_for_token(Comment)['color']
    def luminance(value):
        rgb = [int(value[index:index + 2], 16) / 255 for index in (0, 2, 4)]
        channels = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in rgb]
        return sum(weight * channel for weight, channel in zip((.2126, .7152, .0722), channels))
    for background in ('e8f1e8', 'dde8de'):
        assert (luminance(background) + .05) / (luminance(color) + .05) >= 4.5


def test_soft_theme_retains_keyword_tag_and_number_syntax_colors():
    from notepadview.syntax import style_for_theme
    from pygments.token import Token
    style = style_for_theme('soft')
    for token in (Token.Keyword, Token.Name.Tag, Token.Literal.Number, Token.Literal.String):
        assert style.style_for_token(token)['color']
    assert style.style_for_token(Token.Keyword)['bold']


def test_failed_save_keeps_existing_document(tmp_path):
    from notepadview.document import save_document
    import pytest
    target = tmp_path / 'existing.md'
    target.write_text('original', encoding='utf-8')
    with pytest.raises(UnicodeEncodeError):
        save_document(target, 'bad\ud800')
    assert target.read_text() == 'original'
    assert len(list(tmp_path.iterdir())) == 1


def test_plain_text_rendering_is_literal_and_safe():
    from notepadview.rendering import render_plain_text
    html = render_plain_text('# literal\n**not bold**\n<script>alert(1)</script>')
    assert '<pre' in html and 'white-space:pre-wrap' in html
    assert '# literal\n**not bold**\n&lt;script&gt;alert(1)&lt;/script&gt;' in html
    assert '<script>' not in html
def test_code_preview_is_highlighted_and_inert():
    from notepadview.rendering import render_code_preview
    result = render_code_preview('<script>alert(1)</script>', 'xml')
    assert '<script>' not in result
    assert '&lt;' in result and 'script' in result
    assert 'Content-Security-Policy' in result and '<span' in result
    assert '<pre class="highlight">' in result


