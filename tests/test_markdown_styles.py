import pytest
from PySide6.QtGui import QColor

from notepadview.rendering import parse_markdown
from test_ui import app, window  # noqa: F401


def render(source):
    parser, tokens = parse_markdown(source)
    return parser.renderer.render(tokens, parser.options, {})


def test_safe_color_supports_markdown_and_keeps_html_escaped():
    html = render('<span style="color:#d03050">**中文** ~~旧内容~~</span>')
    assert '<span style="color:#d03050">' in html
    assert '<strong>中文</strong>' in html
    assert '<s>旧内容</s>' in html
    assert '<script>' not in render('<script>alert(1)</script>')


@pytest.mark.parametrize('source', [
    '<span style="color:red;position:fixed">text</span>',
    '<span onclick="alert(1)" style="color:#ff0000">text</span>',
    '<span style="color:url(https://example.com)">text</span>',
    '<span style="color:#ff0000">unclosed',
])
def test_unsupported_html_stays_literal(source):
    assert '<span' not in render(source)


def test_markdown_color_and_strike_actions_preserve_undo(window, monkeypatch):
    from notepadview import editor as module
    monkeypatch.setattr(module.QColorDialog, 'getColor', lambda *a, **k: QColor('#d03050'))
    window.new_document('md')
    window.editor.setPlainText('中文😀')
    window.editor.set_selection(0, 3)
    window.editor.apply_markdown_color()
    assert window.editor.source_text() == '<span style="color:#d03050">中文😀</span>'
    window.editor.undo()
    assert window.editor.source_text() == '中文😀'
    window.editor.set_selection(0, 3)
    menu = window.editor.build_context_menu()
    next(a for a in menu.actions() if a.text() == '删除线').trigger()
    assert window.editor.source_text() == '~~中文😀~~'
    window.editor.undo()
    window.editor.set_selection(0, 0)
    window.editor.apply_markdown_color()
    assert window.editor.source_text().startswith('<span style="color:#d03050">文字</span>')


def test_colored_preview_selection_and_toolbar(window):
    from test_scroll_sync import js, wait_for
    source = '<span style="color:#d03050">中文😀 ~~旧内容~~</span>'
    window.new_document('md')
    window.editor.setPlainText(source)
    window.set_mode('split')
    wait_for(lambda: window._preview_ready and window.selection_sync.enabled())
    assert js(window, 'document.body.innerText.trim()') == '中文😀 旧内容'
    assert js(window, "getComputedStyle(document.querySelector('span')).color") == 'rgb(208, 48, 80)'
    start = source.index('旧内容')
    window.editor.set_selection(start, start + 3)
    wait_for(lambda: window.preview.page().selectedText() == '旧内容')
    assert js(window, "getComputedStyle(document.querySelector('s')).textDecorationLine") == 'line-through'
    strike = next(a for a in window.format_actions if a.toolTip() == '删除线')
    strike.trigger()
    assert '~~~~旧内容~~~~' in window.editor.source_text()


def test_color_markup_inside_code_stays_literal():
    html = render('`<span style="color:#ff0000">code</span>`')
    assert '<span' not in html
    assert '&lt;span' in html
    assert '<span' not in render('    <span style="color:#ff0000">code</span>')


def test_cancel_color_and_readonly_do_not_change_text(window, monkeypatch):
    from notepadview import editor as module
    monkeypatch.setattr(module.QColorDialog, 'getColor', lambda *a, **k: QColor())
    window.new_document('md')
    window.editor.setPlainText('unchanged')
    window.editor.selectAll()
    window.editor.apply_markdown_color()
    assert window.editor.source_text() == 'unchanged'
    window.editor.setReadOnly(True)
    window.editor.apply_markdown_inline('~~', '~~', '文字')
    assert window.editor.source_text() == 'unchanged'
