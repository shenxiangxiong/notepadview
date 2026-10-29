import pytest

from notepadview.rendering import render_html, render_plain_text, render_code_preview


def test_markdown_blocks_carry_source_ranges():
    html = render_html('# 标题\n\n正文\n\n```python\nx = 1\n```\n')
    assert 'data-source-line="0"' in html
    assert 'data-source-line="2"' in html
    assert 'data-source-line="4"' in html


def test_large_preview_chunks_keep_absolute_line_positions():
    html = render_plain_text(('x' * 1500 + '\n') * 450)
    assert 'data-source-line="200"' in html
    assert 'data-source-line="400"' in html


@pytest.mark.parametrize('render', [render_plain_text, lambda text: render_code_preview(text, 'python')])
def test_text_preview_has_line_mapping_origin(render):
    assert 'data-source-line="0"' in render('one\ntwo\n')


from PySide6.QtTest import QTest
from PySide6.QtWebEngineCore import QWebEngineScript
from test_ui import app, window  # noqa: F401


def wait_for(predicate):
    for _ in range(200):
        if predicate():
            return
        QTest.qWait(25)
    assert predicate()


def js(window, script):
    result = []
    window.preview.page().runJavaScript(script, QWebEngineScript.ApplicationWorld, result.append)
    wait_for(lambda: bool(result))
    return result[0]


@pytest.mark.parametrize('language', ['txt', 'python', 'json', 'md'])
def test_real_bidirectional_scrolling(window, language):
    window.new_document(language)
    source = '\n\n'.join(f'段落 {i}' for i in range(250)) if language == 'md' else '\n'.join(f'line {i}' for i in range(500))
    window.editor.setPlainText(source)
    window.set_mode('split')
    window.render_preview()
    wait_for(lambda: window._preview_ready)
    wait_for(lambda: js(window, '!!globalThis.nvScrollMap'))
    QTest.qWait(150)
    editor = window.editor
    editor.setFirstVisibleLine(150)
    wait_for(lambda: window.scroll_position > 500)
    QTest.qWait(200)
    mapped = js(window, 'nvScrollMap.toLine(scrollY)')
    assert abs(mapped - 150) < 4
    js(window, 'window.scrollTo(0,nvScrollMap.toY(300))')
    QTest.qWait(500)
    assert editor.firstVisibleLine() >= 290, (editor.firstVisibleLine(), window.scroll_sync._busy, window.scroll_sync._direction, js(window, '[scrollY,nvScrollMap.toLine(scrollY)]'))
    assert abs(editor.firstVisibleLine()-300) < 4
    QTest.qWait(150)
    js(window, 'window.scrollTo(0,document.documentElement.scrollHeight)')
    wait_for(lambda: editor.verticalScrollBar().value() == editor.verticalScrollBar().maximum())
    QTest.qWait(150)
    editor.setFirstVisibleLine(0)
    wait_for(lambda: window.scroll_position < 100)
    QTest.qWait(150)
    window.set_mode('source')
    position = window.scroll_position
    editor.setFirstVisibleLine(100)
    QTest.qWait(150)
    assert abs(window.scroll_position-position) < 1
    window.set_mode('split')
    window.editor.setPlainText(source + '\nend')
    window.render_preview()
    wait_for(lambda: window._preview_ready)
    QTest.qWait(200)
    editor.setFirstVisibleLine(200)
    try:
        wait_for(lambda: abs(js(window, 'nvScrollMap.toLine(scrollY)')-200) < 4)
    except AssertionError:
        pytest.fail(str(dict(source=editor.firstVisibleLine(),
                             preview=js(window, 'JSON.stringify([scrollY,nvScrollMap.toLine(scrollY), document.documentElement.dataset.previewGeneration])'),
                             busy=window.scroll_sync._busy, direction=window.scroll_sync._direction,
                             pending=window.scroll_sync._pending_direction,
                             ready=window._preview_ready, generation=window._preview_generation,
                             selection=editor.selection_range())))


def test_preview_notification_does_not_override_pending_source_scroll(window):
    window.new_document('txt')
    window.set_mode('split')
    window._preview_ready = True
    sync = window.scroll_sync
    sync._busy = False
    sync._queue('source')
    sync._queue('preview')
    assert sync._direction == 'source'
    sync.timer.stop()


def test_preview_scroll_during_source_suppression_is_not_lost(window):
    window.new_document('txt')
    window.set_mode('split')
    window._preview_ready = True
    sync = window.scroll_sync
    sync.timer.stop()
    sync._busy = True
    sync._direction = 'source'
    sync._queue('preview')
    assert sync._pending_direction == 'preview'
    sync._busy = False


def test_source_scroll_during_preview_suppression_is_not_lost(window):
    window.new_document('txt')
    window.editor.setPlainText('\n'.join(f'line {i}' for i in range(500)))
    window.set_mode('split')
    window.render_preview()
    wait_for(lambda: window._preview_ready)
    wait_for(lambda: js(window, '!!globalThis.nvScrollMap'))
    QTest.qWait(150)
    sync = window.scroll_sync
    sync._busy = True
    sync._direction = 'preview'
    old_serial = sync._serial
    window.editor.setFirstVisibleLine(200)
    assert sync._serial > old_serial
    wait_for(lambda: abs(js(window, 'nvScrollMap.toLine(scrollY)') - 200) < 4)


def test_html_and_single_view_disable_sync(window):
    window.new_document('html')
    window._preview_ready = True
    window.set_mode('split')
    assert not window.scroll_sync.enabled()
    window.new_document('txt')
    window._preview_ready = True
    window.set_mode('source')
    assert not window.scroll_sync.enabled()
    window.set_mode('split')
    window._preview_ready = True
    assert window.scroll_sync.enabled()
