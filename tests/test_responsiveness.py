import threading
import tracemalloc

import pytest
from PySide6.QtTest import QTest

from test_ui import app, window  # noqa: F401
from test_scroll_sync import wait_for


def test_switch_large_documents_stays_responsive(window, tmp_path):
    import time
    for name in ('first.txt', 'second.txt'):
        path = tmp_path / name
        path.write_text('sample plain text line 中文\n' * 100000, encoding='utf-8')
        window.open_document_path(path)
    QTest.qWait(500)
    elapsed = []
    for index in (0, 1, 0, 1):
        started = time.perf_counter()
        window.activate_document(index)
        elapsed.append(time.perf_counter() - started)
    print('switch-ms', [round(value * 1000) for value in elapsed])
    assert max(elapsed) < 0.25


def test_format_detection_cache_invalidates_on_edit_rename_and_manual_language(window, tmp_path):
    path = tmp_path / 'sample.txt'
    path.write_text('ordinary text', encoding='utf-8')
    window.open_document_path(path)
    session = window.current_session
    window.editor.setPlainText('{"value": 1}')
    session.refresh_text_format()
    assert session.document_format == 'json'
    window.editor.setPlainText('ordinary text again')
    session.refresh_text_format()
    assert session.document_format == 'txt'
    session.manual_format = True
    session.document_format = 'md'
    session.refresh_text_format()
    assert session.document_format == 'md'
    session.path = tmp_path / 'sample.py'
    session.refresh_text_format()
    assert session.document_format == 'python'
    assert not session.manual_format


def test_click_after_switching_large_documents(window, tmp_path):
    import time
    from PySide6.QtCore import Qt, QTimer
    for name in ('one.txt', 'two.txt'):
        path = tmp_path / name
        path.write_text('sample text 中文\n' * 100000, encoding='utf-8')
        window.open_document_path(path)
    gaps = []
    last = time.perf_counter()
    def tick():
        nonlocal last
        now = time.perf_counter()
        gaps.append(now - last)
        last = now
    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(20)
    for index in (0, 1, 0, 1):
        window.activate_document(index)
        QTest.mouseClick(window.editor, Qt.LeftButton)
        QTest.qWait(1200)
    timer.stop()
    print('click-heartbeat-ms', round(max(gaps) * 1000))
    assert max(gaps) < 0.5


def test_large_statistics_run_off_ui_thread(window, monkeypatch):
    import notepadview.window as module
    original = module.re.finditer
    threads = []
    def measured(pattern, text, *args, **kwargs):
        if pattern == r'[\u4e00-\u9fff]|[A-Za-z0-9_]+':
            threads.append(threading.get_ident())
        return original(pattern, text, *args, **kwargs)
    window.new_document('txt')
    window.editor.setPlainText('sample 中文\n' * 100000)
    monkeypatch.setattr(module.re, 'finditer', measured)
    window.update_statistics()
    assert threading.get_ident() not in threads
    wait_for(lambda: window.editor.document().statistics_revision == window.editor.document().revision)
    assert window.editor.document().statistics == (300000, 1000000)


def test_statistics_discard_stale_result_after_edit_and_switch(window):
    window.new_document('txt')
    first = window.editor.document()
    window.editor.setPlainText('word\n' * 100000)
    window.update_statistics()
    window.editor.setPlainText('updated')
    window.update_statistics()
    window.new_document('txt')
    window.editor.setPlainText('second document')
    window.update_statistics()
    wait_for(lambda: window._stats_work is None and window._pending_stats is None)
    assert first.statistics == (1, 7)
    assert first.statistics_revision == first.revision
    assert window._stats_count == 2
    assert window._stats_char_count == 15


@pytest.mark.parametrize('language', ['py', 'txt', 'md'])
def test_warm_document_switch_preserves_preview_page_and_mapping(window, tmp_path, monkeypatch, language):
    from test_scroll_sync import js
    for name in (f'warm-one.{language}', f'warm-two.{language}'):
        path = tmp_path / name
        path.write_text(f'# {name}\n' + 'value = 123\n' * 25000, encoding='utf-8')
        window.open_document_path(path)
        window.set_mode('split')
        wait_for(lambda: window._preview_ready and window.selection_sync.enabled())
        js(window, 'globalThis.warmMarker = 123; true')
    renders = []
    original = window._render_preview_work
    def measured(work):
        renders.append(work['generation'])
        original(work)
    monkeypatch.setattr(window, '_render_preview_work', measured)
    import time
    elapsed = []
    for index in (0, 1, 0, 1):
        started = time.perf_counter()
        window.activate_document(index)
        window.set_mode('split')
        wait_for(lambda: window._preview_ready and window.selection_sync.enabled())
        elapsed.append(time.perf_counter() - started)
        expected = f'warm-{"one" if index == 0 else "two"}.{language}'
        assert expected in js(window, 'document.body.textContent.slice(0, 100)')
        assert js(window, 'globalThis.warmMarker') == 123
        assert window.selection_sync.mapping.map_range(2, 6) == ((0, 4) if language == 'md' else (2, 6))
        window.editor.setFirstVisibleLine(200)
        QTest.qWait(600)
    print('warm-preview-ms', [round(value * 1000) for value in elapsed])
    assert max(elapsed) < 0.5
    assert not renders
    window.editor.insertPlainText('edited_value = 456\n')
    wait_for(lambda: 'edited_value' in js(window, 'document.body.textContent'))
    window.editor.document().setModified(False)
    window.activate_document(0)
    window.set_mode('split')
    wait_for(lambda: window._preview_ready and window.selection_sync.enabled())
    window.activate_document(1)
    window.set_mode('split')
    wait_for(lambda: window._preview_ready and window.selection_sync.enabled())
    assert 'edited_value' in js(window, 'document.body.textContent')
    closed_session = window.current_session
    assert window.close_document_session(closed_session)
    assert all(key[0] != id(closed_session) for key in window._preview_cache)


def test_plain_selection_mapping_has_bounded_memory():
    from notepadview.selection_sync import character_pairs
    source = '中文 example line\n' * 20000
    tracemalloc.start()
    try:
        mapping = character_pairs(source, [dict(line=0, end=None, text=source, start=0)])
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024 * 1024
    assert mapping.map_range(2, len(source) - 2) == (2, len(source) - 2)


@pytest.mark.parametrize('newline', ['\r\n', '\r'])
def test_windows_pasted_python_maps_without_fuzzy_matching(monkeypatch, newline):
    from pathlib import Path
    import notepadview.selection_sync as module
    source = (Path(__file__).parents[1] / 'notepadview' / 'diagram_formats.py').read_text(encoding='utf-8')
    pasted = source.replace('\n', newline)

    def unexpected_matcher(*args, **kwargs):
        raise AssertionError('Line-ending differences must not invoke quadratic fuzzy matching')

    monkeypatch.setattr(module, 'SequenceMatcher', unexpected_matcher)
    mapping = module.character_pairs(pasted, [dict(line=0, end=None, text=source, start=0)])
    for text in ('MAX_NODES = 5000', 'def identifier():', source.splitlines()[-1]):
        start = pasted.rindex(text)
        expected = source.rindex(text)
        assert mapping.map_range(start, start + len(text)) == (expected, expected + len(text))
        assert mapping.map_range(expected, expected + len(text), reverse=True) == (start, start + len(text))


def test_text_loading_does_not_scan_each_character_in_python(tmp_path):
    from notepadview.document import load_text_document
    import sys
    source = '中文 sample line\n' * 20000
    path = tmp_path / 'sample.txt'
    path.write_text(source, encoding='utf-8')
    calls = 0

    def count_calls(frame, event, arg):
        nonlocal calls
        if event == 'c_call' and arg is ord:
            calls += 1

    sys.setprofile(count_calls)
    try:
        assert load_text_document(path)[0] == source
    finally:
        sys.setprofile(None)
    assert calls < 100


def test_medium_markdown_preview_runs_off_ui_thread(window, monkeypatch):
    import notepadview.window as module
    original = module.render_html
    threads = []

    def measured(*args):
        threads.append(threading.get_ident())
        return original(*args)

    window.new_document('md')
    window.editor.setPlainText('# heading\n\n' + 'paragraph **bold**\n\n' * 2000)
    monkeypatch.setattr(module, 'render_html', measured)
    window.render_preview()
    assert threading.get_ident() not in threads
    wait_for(lambda: window._preview_ready)
    assert threads and threading.get_ident() not in threads


def test_large_outline_does_not_parse_on_ui_thread(window, monkeypatch):
    import notepadview.window as module
    original = module.markdown_headings
    threads = []

    def measured(source):
        threads.append(threading.get_ident())
        return original(source)

    window.new_document('md')
    monkeypatch.setattr(module, 'markdown_headings', measured)
    window.editor.setPlainText('# heading\n\n' + 'paragraph **bold**\n\n' * 20000)
    window.update_outline()
    assert threading.get_ident() not in threads
    wait_for(lambda: window.outline.count() == 1)
    assert threads and threading.get_ident() not in threads


def test_stale_outline_is_not_applied(window):
    window.new_document('md')
    window.editor.setPlainText('# old\n\n' + 'paragraph\n\n' * 30000)
    window.update_outline()
    window.editor.setPlainText('# current')
    window.update_outline()
    QTest.qWait(600)
    assert window.outline.count() == 1
    assert window.outline.item(0).text() == 'current'


def test_large_file_open_and_clipboard_paste_preserve_content(window, tmp_path):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from time import perf_counter
    source = '# heading\n\n' + '中文 sample **bold**\n\n' * 20000
    path = tmp_path / 'large.md'
    path.write_text(source, encoding='utf-8')
    assert window.open_document_path(path)
    assert window.editor.source_text() == source
    clipboard = QApplication.clipboard()
    previous = clipboard.text()
    ticks = []
    timer = QTimer(window)
    timer.setInterval(20)
    timer.timeout.connect(lambda: ticks.append(perf_counter()))
    timer.start()
    try:
        clipboard.setText(source + 'pasted-end')
        window.editor.selectAll()
        window.editor.paste()
        actual = window.editor.source_text().replace('\r\n', '\n')
        assert actual == source + 'pasted-end', 'Clipboard paste must preserve all text'
        wait_for(lambda: window._preview_ready and window.selection_sync.enabled())
        assert len(ticks) >= 2
        assert max((b-a for a, b in zip(ticks, ticks[1:])), default=0) < 1.0
        window.editor.undo()
        assert window.editor.source_text() == source
    finally:
        timer.stop()
        clipboard.setText(previous)


def test_pasted_diagram_source_remains_interactive(window):
    from pathlib import Path
    from time import perf_counter
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication
    source = (Path(__file__).parents[1] / 'notepadview' / 'diagram_formats.py').read_text(encoding='utf-8')
    window.new_document('python')
    window.set_mode('split')
    wait_for(lambda: window._preview_ready and window.selection_sync.enabled())
    clipboard = QApplication.clipboard()
    previous = clipboard.text()
    ticks = []
    timer = QTimer(window)
    timer.setInterval(20)
    timer.timeout.connect(lambda: ticks.append(perf_counter()))
    timer.start()
    try:
        clipboard.setText(source)
        window.editor.paste()
        wait_for(lambda: window._preview_ready and window.selection_sync.enabled())
        for _ in range(5):
            QTest.mouseClick(window.editor, Qt.LeftButton)
            QTest.qWait(50)
        pasted = window.editor.source_text()
        assert pasted.replace('\r\n', '\n') == source, 'Pasted source must remain complete'
        text = 'MAX_NODES = 5000'
        start = pasted.index(text)
        window.editor.set_selection(start, start + len(text))
        wait_for(lambda: window.preview.page().selectedText() == text)
        assert len(ticks) >= 2
        assert max(b-a for a, b in zip(ticks, ticks[1:])) < 1.0
        window.editor.undo()
        assert window.editor.source_text() == ''
    finally:
        timer.stop()
        clipboard.setText(previous)


def test_edit_invalidates_inflight_preview_before_debounce(window, monkeypatch):
    from threading import Event
    import notepadview.window as module
    started, release = Event(), Event()
    original = module.render_code_preview

    def delayed(*args):
        started.set()
        release.wait(5)
        return original(*args)

    window.new_document('python')
    window.set_mode('split')
    window.editor.setPlainText('print("old")\n' * 2000)
    monkeypatch.setattr(module, 'render_code_preview', delayed)
    published = []
    original_publish = window._publish_preview

    def publish(html, generation):
        if generation == window._preview_generation:
            published.append(html)
        original_publish(html, generation)

    monkeypatch.setattr(window, '_publish_preview', publish)
    window.render_preview()
    wait_for(started.is_set)
    try:
        window.editor.set_selection(0, 10000)
        window.editor.insertPlainText('new')
        window.timer.stop()
        release.set()
        wait_for(lambda: window._preview_work is None)
        assert published == [], 'An edit must reject old HTML before the debounce timer fires'
        assert not window._preview_ready
    finally:
        release.set()


def test_selection_mapping_not_built_in_source_only_mode(window, monkeypatch):
    import notepadview.selection_sync as module
    calls = []
    original = module.character_pairs

    def measured(*args):
        calls.append(True)
        return original(*args)

    monkeypatch.setattr(module, 'character_pairs', measured)
    window.new_document('txt')
    window.set_mode('source')
    window.editor.setPlainText('text\n' * 10000)
    window.render_preview()
    wait_for(lambda: window._preview_ready)
    QTest.qWait(200)
    assert calls == []
