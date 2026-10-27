import pytest
from PySide6.QtWidgets import QApplication


def test_scintilla_editor_can_share_a_document_between_views():
    from pyside6_scintilla import ScintillaEdit

    app = QApplication.instance() or QApplication([])
    first = ScintillaEdit()
    second = ScintillaEdit()
    try:
        first.setText('probe')
        shared_document = first.get_doc()
        second.set_doc(shared_document)
        assert bytes(second.getText(second.length() + 1)).rstrip(b'\0') == b'probe'
        second.setText('shared')
        assert bytes(first.getText(first.length() + 1)).rstrip(b'\0') == b'shared'
    finally:
        first.close()
        second.close()
        app.processEvents()


def test_adapter_utf8_selection_undo_and_documents():
    from notepadview.editor import SourceEditor, SourceDocument
    app = QApplication.instance() or QApplication([])
    editor = SourceEditor()
    first = SourceDocument()
    first.setPlainText('中文 alpha')
    editor.set_source_document(first)
    editor.set_selection(3, 8)
    assert editor.selected_text() == 'alpha'
    editor.replace_selection('beta')
    assert editor.source_text() == '中文 beta'
    assert first.isModified() and first.isUndoAvailable()
    second = SourceDocument()
    editor.set_source_document(second)
    editor.setPlainText('second')
    editor.set_source_document(first)
    editor.undo()
    assert editor.source_text() == '中文 alpha'
    editor.set_source_document(SourceDocument())
    editor.close()


def test_highlight_preserves_tabs_and_crlf_byte_positions():
    from notepadview.editor import SourceEditor
    app = QApplication.instance() or QApplication([])
    editor = SourceEditor()
    editor.setPlainText('select\t中文\r\nfrom Users where id=42')
    editor.set_language('sql')
    data = editor.source_text().encode('utf-8')
    assert editor.styleAt(data.index(b'select')) == editor.styleAt(data.index(b'from'))
    assert editor.styleAt(data.index(b'42')) != editor.styleAt(data.index(b'Users'))
    editor.close()


def test_alt_reverse_first_column_drag_and_native_block_editing():
    from PySide6.QtCore import Qt, QPoint
    from PySide6.QtTest import QTest
    from notepadview.editor import SourceEditor
    app = QApplication.instance() or QApplication([])
    editor = SourceEditor()
    editor.resize(600, 350)
    editor.set_language('txt')
    editor.setPlainText('abc\ndef\nghi')
    editor.show()
    app.processEvents()
    def point(position):
        return QPoint(editor.pointXFromPosition(position),
                      editor.pointYFromPosition(position) + editor.textHeight(0) // 2)
    QTest.mousePress(editor.viewport(), Qt.LeftButton, Qt.AltModifier, point(9))
    QTest.mouseMove(editor.viewport(), point(0))
    QTest.mouseRelease(editor.viewport(), Qt.LeftButton, Qt.AltModifier, point(0))
    assert editor.selectionIsRectangle()
    assert editor.selections() == 3
    assert editor.selected_text().splitlines() == ['a', 'd', 'g']
    editor.copy()
    assert app.clipboard().text().splitlines() == ['a', 'd', 'g']
    editor.cut()
    assert editor.source_text() == 'bc\nef\nhi'
    editor.undo()
    assert editor.source_text() == 'abc\ndef\nghi'
    editor.close()


def test_native_alt_rectangular_drag():
    from scripts.scintilla_probe import probe
    probe()


def test_utf8_language_styles():
    from notepadview.editor import SourceEditor
    app = QApplication.instance() or QApplication([])
    editor = SourceEditor()
    editor.setPlainText('{"中文": 42, "ok": true}')
    editor.set_language('json')
    number = editor.source_text().encode().index(b'42')
    keyword = editor.source_text().encode().index(b'true')
    assert editor.styleAt(number) != editor.styleAt(keyword)
    assert editor.styleAt(number) != editor.styleAt(1)
    editor.close()


def test_xml_tag_attribute_and_string_have_distinct_styles():
    from notepadview.editor import SourceEditor
    app = QApplication.instance() or QApplication([])
    editor = SourceEditor()
    editor.setPlainText('<root id="value"/>')
    editor.set_language('xml')
    assert len({editor.styleAt(1), editor.styleAt(6), editor.styleAt(10)}) == 3
    editor.close()


def test_large_code_content_skips_synchronous_lexing(monkeypatch):
    from threading import current_thread, main_thread
    import notepadview.editor as editor_module
    from notepadview.editor import SourceEditor

    app = QApplication.instance() or QApplication([])
    editor = SourceEditor()
    editor.set_language('json')
    editor.highlight_timer.stop()

    original_lexer = editor_module.get_lexer_by_name

    def fail_if_lexed(*args, **kwargs):
        assert current_thread() is not main_thread(), 'large content must not lex synchronously'
        return original_lexer(*args, **kwargs)

    monkeypatch.setattr(editor_module, 'get_lexer_by_name', fail_if_lexed)
    try:
        editor.setPlainText('this is not json ' * 30000)
        assert editor.source_text().startswith('this is not json')
    finally:
        editor.highlight_timer.stop()
        editor.close()


def test_large_content_is_highlighted_incrementally():
    from PySide6.QtTest import QTest
    from notepadview.editor import SourceEditor
    from time import monotonic
    app = QApplication.instance() or QApplication([])
    editor = SourceEditor()
    editor.set_language('json')
    editor.setPlainText('{"value": 42, "ok": true}\n' * 14000)
    deadline = monotonic() + 5
    while editor.styleAt(1) == 0 and monotonic() < deadline:
        QTest.qWait(20)
    assert editor.styleAt(1) != 0
    editor.close()


def test_cursor_position_does_not_read_full_text(monkeypatch):
    from notepadview.editor import SourceEditor
    app = QApplication.instance() or QApplication([])
    editor = SourceEditor()
    editor.setPlainText('中文\n第二行😀abc')
    editor.setSel(editor.length(), editor.length())
    monkeypatch.setattr(editor, 'getText', lambda *_: pytest.fail('full document read'))
    assert editor.cursor_position() == (2, 8)
    assert editor.selection_range() == (10, 10)
    editor.close()


def test_native_paste_finishes_background_highlight():
    from time import monotonic, sleep
    from PySide6.QtTest import QTest
    from notepadview.editor import SourceEditor
    app = QApplication.instance() or QApplication([])
    editor = SourceEditor()
    editor.set_language('json')
    app.clipboard().setText('not json text\n' * 30000)
    editor.paste()
    deadline = monotonic() + 10
    try:
        while editor.document().highlight_key != (editor.document().revision, 'json'):
            assert monotonic() < deadline
            QTest.qWait(10)
            sleep(.001)
    finally:
        editor.close()


def test_unchanged_document_highlight_is_reused(monkeypatch):
    import notepadview.editor as module
    app = QApplication.instance() or QApplication([])
    editor = module.SourceEditor()
    editor.set_language('json')
    editor.setPlainText('{"value": 42}')
    monkeypatch.setattr(module, 'get_lexer_by_name', lambda *_args, **_kwargs: pytest.fail('repeated lexing'))
    editor.rehighlight()
    editor.set_theme('dark')
    editor.close()


def test_detaching_a_caller_owned_document_keeps_it_alive():
    from PySide6.QtCore import QCoreApplication, QEvent
    from notepadview.editor import SourceEditor, SourceDocument
    app = QApplication.instance() or QApplication([])
    editor = SourceEditor()
    first = SourceDocument()
    first.setPlainText('retained')
    editor.set_source_document(first)
    editor.set_source_document(SourceDocument())
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert first.length() == len('retained')
    editor.set_source_document(first)
    assert editor.source_text() == 'retained'
    editor.close()
