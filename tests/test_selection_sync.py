import pytest
from PySide6.QtTest import QTest

from test_ui import app, window  # noqa: F401
from test_scroll_sync import js, wait_for


@pytest.mark.parametrize('language,source,text', [
    ('txt', '重复 😀中文\n重复 😀中文', '😀中文'),
    ('python', '# 中文\nvalue = "重复"\nother = "重复"', '重复'),
    ('json', '{"first": "😀中文", "last": "😀中文"}', '😀中文'),
    ('md', '# 标题\n\n第一段 **重复文字**。\n\n第二段 **重复文字**。', '重复文字'),
])
def test_source_selection_is_mirrored(window, language, source, text):
    window.new_document(language)
    window.editor.setPlainText(source)
    window.set_mode('split')
    window.render_preview()
    wait_for(lambda: window._preview_ready)
    start = source.rindex(text)
    window.editor.setFocus()
    window.editor.set_selection(start, start + len(text))
    wait_for(lambda: window.preview.page().selectedText() == text)
    assert window.editor.hasFocus()
    window.editor.set_selection(start, start)
    wait_for(lambda: not window.preview.page().selectedText())


def test_continuous_source_selection_updates_preview_without_waiting_for_pause(window):
    window.new_document('python')
    window.editor.setPlainText('value = "selection"\n' * 300)
    window.set_mode('split')
    window.render_preview()
    wait_for(window.selection_sync.enabled)
    window.editor.setFocus()
    mirrored = []
    for end in range(10, 70):
        window.editor.set_selection(0, end)
        QTest.qWait(10)
        mirrored.append(len(window.preview.page().selectedText()))
    assert sum(length > 0 for length in mirrored[:30]) >= 10, mirrored
    expected = window.editor.selected_text().replace('\r\n', '\n')
    wait_for(lambda: window.preview.page().selectedText().replace('\r\n', '\n') == expected)


def test_old_preview_does_not_clear_new_source_selection(window):
    window.new_document('python')
    window.editor.setPlainText('first = 1\nsecond = 2\n')
    window.set_mode('split')
    window.render_preview()
    wait_for(window.selection_sync.enabled)
    window.editor.setFocus()
    window.editor.set_selection(0, 5)
    wait_for(lambda: window.preview.page().selectedText() == 'first')
    window.selection_sync.preview_timer.stop()
    js(window, 'getSelection().removeAllRanges()')
    window.editor.set_selection(10, 16)
    window.selection_sync.timer.stop()
    window.selection_sync._from_preview()
    QTest.qWait(20)
    assert window.editor.selection_range() == (10, 16)


def test_code_edit_updates_preview_without_navigation(window):
    window.new_document('python')
    window.editor.setPlainText('value = 123\n' * 400)
    window.set_mode('split')
    window.render_preview()
    wait_for(window.selection_sync.enabled)
    loads = []
    window.preview.loadStarted.connect(lambda: loads.append(True))
    window.editor.set_selection(8, 11)
    window.editor.insertPlainText('456')
    window.editor.set_selection(0, 11)
    wait_for(lambda: window.selection_sync.enabled()
             and window.preview.page().selectedText() == 'value = 456')
    assert not loads, 'Code edits must update blocks without reloading the page'
    assert 'value = 456' in js(window, 'document.body.innerText')
    assert js(window, 'document.body.textContent') == window.editor.source_text().replace('\r\n', '\n')
    window.editor.undo()
    window.editor.set_selection(0, 11)
    wait_for(lambda: window.selection_sync.enabled()
             and window.preview.page().selectedText() == 'value = 123')
    assert not loads
    window.new_document('python')
    window.editor.setPlainText('other = "中文😀"')
    window.render_preview()
    wait_for(window.selection_sync.enabled)
    assert js(window, 'document.body.textContent') == 'other = "中文😀"'


def test_deleting_first_line_preserves_unchanged_preview_blocks(window):
    window.new_document('python')
    window.editor.setPlainText(''.join(f'value_{i} = {i}\n' for i in range(1200)))
    window.set_mode('split')
    window.render_preview()
    wait_for(window.selection_sync.enabled)
    js(window, 'globalThis.savedTail = document.body.lastElementChild; true')
    window.editor.set_selection(0, len('value_0 = 0\n'))
    window.editor.insertPlainText('')
    wait_for(window.selection_sync.enabled)
    assert js(window, 'savedTail === document.body.lastElementChild')
    assert js(window, 'document.body.textContent') == window.editor.source_text().replace('\r\n', '\n')


def test_selection_recovers_after_repeated_deletes(window):
    from pathlib import Path
    from time import perf_counter
    source = (Path(__file__).parents[1] / 'notepadview' / 'window.py').read_text(encoding='utf-8')
    window.new_document('python')
    window.editor.setPlainText(source)
    window.set_mode('split')
    window.render_preview()
    wait_for(window.selection_sync.enabled)
    elapsed = []
    for _ in range(5):
        window.editor.set_selection(0, 20)
        window.editor.insertPlainText('')
        window.editor.set_selection(0, 30)
        started = perf_counter()
        expected = window.editor.selected_text().replace('\r\n', '\n')
        wait_for(lambda: window.selection_sync.enabled()
                 and window.preview.page().selectedText().replace('\r\n', '\n') == expected)
        elapsed.append(round((perf_counter() - started) * 1000))
    print('delete-to-selection-ms:', elapsed)
    assert max(elapsed) < 3000, elapsed
    assert window.editor.selection_range() == (0, 30)
    for index in range(50):
        window.editor.set_selection(index, index + 100)
        window.editor.insertPlainText('')
        window.editor.undo()
        window.editor.set_selection(index, index + 30)
        QTest.qWait(10)
    expected = window.editor.selected_text().replace('\r\n', '\n')
    wait_for(lambda: window.selection_sync.enabled()
             and window.preview.page().selectedText().replace('\r\n', '\n') == expected)


def test_preview_selection_maps_repeated_markdown_text(window):
    source = '# 标题\n\n第一段 **重复文字**。\n\n第二段 **重复文字**。'
    window.new_document('md')
    window.editor.setPlainText(source)
    window.set_mode('split')
    window.render_preview()
    wait_for(lambda: window._preview_ready)
    QTest.qWait(200)
    js(window, '''(() => { const node = document.querySelectorAll('strong')[1].firstChild;
        const range = document.createRange(); range.setStart(node,0); range.setEnd(node,4);
        const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range); })()''')
    start = source.rindex('重复文字')
    wait_for(lambda: window.editor.selection_range() == (start, start+4))
    js(window, 'getSelection().removeAllRanges()')
    wait_for(lambda: window.editor.selection_range()[0] == window.editor.selection_range()[1])


@pytest.mark.parametrize('language,mode', [('html', 'split'), ('txt', 'source')])
def test_selection_sync_is_disabled_outside_supported_split(window, language, mode):
    window.new_document(language)
    window.editor.setPlainText('plain text')
    window.set_mode(mode)
    window.render_preview()
    wait_for(lambda: window._preview_ready)
    window.editor.set_selection(0, 5)
    QTest.qWait(200)
    assert not window.selection_sync.enabled()
    assert not window.preview.page().selectedText()


def test_markdown_visible_mapping_and_refresh(window):
    source = '# 标题\n\n**加粗** 和 [链接](https://example.com) 与 `代码😀`\n\n下一段'
    window.new_document('md')
    window.editor.setPlainText(source)
    window.set_mode('split')
    window.render_preview()
    wait_for(lambda: window._preview_ready)
    start = source.index('加粗')
    end = source.index('代码😀') + len('代码😀')
    window.editor.set_selection(start, end)
    wait_for(lambda: window.preview.page().selectedText() == '加粗 和 链接 与 代码😀')
    window.render_preview()
    wait_for(lambda: window._preview_ready)
    wait_for(lambda: js(window, 'getSelection().toString()') == '加粗 和 链接 与 代码😀')
    window.editor.set_selection(end, end)
    wait_for(lambda: not window.preview.page().selectedText())
    window.preview.setFocus()
    js(window, '''(() => { const node = document.querySelector('code').firstChild;
        const range = document.createRange(); range.selectNodeContents(node);
        getSelection().removeAllRanges(); getSelection().addRange(range); })()''')
    wait_for(lambda: window.editor.selected_text() == '代码😀')
    assert window.preview.hasFocus()
