import codecs

import pytest
from PySide6.QtWidgets import QMessageBox, QToolButton

from notepadview import document
from test_ui import app, window  # noqa: F401


@pytest.mark.parametrize('encoding,expected', [
    ('utf-8', b'hello\r\n'),
    ('utf-8-sig', codecs.BOM_UTF8 + b'hello\r\n'),
    ('utf-16-be', codecs.BOM_UTF16_BE + 'hello\r\n'.encode('utf-16-be')),
    ('utf-16', codecs.BOM_UTF16_LE + 'hello\r\n'.encode('utf-16-le')),
    ('ansi', b'hello\r\n'),
])
def test_actual_bytes_and_forced_reload(tmp_path, encoding, expected):
    path = tmp_path / 'note.txt'
    document.save_document(path, 'hello\n', encoding, '\r\n')
    assert path.read_bytes() == expected
    assert document.load_text_document(path, encoding=encoding) == ('hello\n', encoding, '\r\n')
    assert document.load_text_document(path)[0] == 'hello\n'


def test_ansi_uses_system_codec_and_strict_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(document, 'ansi_encoding', lambda: 'cp1252')
    path = tmp_path / 'western.txt'
    path.write_bytes(b'caf\xe9')
    assert document.load_text_document(path, encoding='ansi') == ('café', 'ansi', '\n')
    assert document.load_text_document(path)[1] == 'ansi'
    document.save_document(path, '€', 'ansi')
    assert path.read_bytes() == b'\x80'
    with pytest.raises(UnicodeEncodeError):
        document.save_document(path, '中文', 'ansi')
    assert path.read_bytes() == b'\x80'
    with pytest.raises(UnicodeDecodeError):
        document.load_text_document(path, encoding='utf-8')


def test_menu_labels_states_and_conversion_undo(window, tmp_path):
    assert isinstance(window.encoding_label, QToolButton)
    assert not window.encoding_label.isEnabled()
    window.new_document('txt')
    actions = window.encoding_menu.actions()
    labels = ['ANSI编码', 'UTF-8编码', 'UTF-8BOM编码', 'UTF-16 BE BOM编码', 'UTF-16 LE BOM编码']
    assert [a.text() for a in actions] == labels + [''] + ['转为' + label for label in labels]
    window.encoding_menu.aboutToShow.emit()
    assert all(not a.isEnabled() for a in actions[:5])
    assert all(a.isEnabled() for a in actions[6:])
    window.editor.insertPlainText('hello')
    path = tmp_path / 'note.txt'
    assert window.save_to(path)
    window.editor.insertPlainText('!')
    selection = window.editor.selection_state()
    actions[8].trigger()
    assert window.current_session.encoding == 'utf-8-sig'
    assert 'UTF-8BOM' in window.encoding_label.text()
    assert window.editor.selection_state() == selection
    window.editor.undo()
    assert window.editor.source_text() == 'hello'
    assert window.current_session.document.isModified()
    assert window.save_document()
    assert path.read_bytes() == codecs.BOM_UTF8 + b'hello'
    assert not window.current_session.document.isModified()


def test_reload_is_transactional_and_preserves_manual_type(window, tmp_path, monkeypatch):
    path = tmp_path / 'note.txt'
    path.write_bytes(b'original\r\n')
    assert window.open_document_path(path)
    window.set_document_type('json')
    window.set_mode('split')
    window.editor.insertPlainText('edit')
    session = window.current_session
    selection = window.editor.selection_state()
    before = window.editor.source_text()
    prompts = []
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: prompts.append(args) or QMessageBox.No)
    assert not window.reload_encoding('utf-8-sig')
    assert prompts and window.editor.source_text() == before
    assert session.encoding == 'utf-8' and session.document.isModified()
    assert window.editor.selection_state() == selection
    errors = []
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: errors.append(args))
    path.write_bytes(b'\xff')
    assert not window.reload_encoding('utf-8')
    assert errors and window.editor.source_text() == before
    assert window.editor.selection_state() == selection
    assert session.encoding == 'utf-8' and session.document.isModified()
    path.write_bytes(codecs.BOM_UTF16_BE + '磁盘\r\n'.encode('utf-16-be'))
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.Yes)
    assert window.reload_encoding('utf-16-be')
    assert window.editor.source_text() == '磁盘\n'
    assert session.encoding == 'utf-16-be' and session.newline == '\r\n'
    assert session.manual_format and session.document_format == 'json'
    assert window.mode == 'split' and not session.document.isModified()


def test_conversion_failure_draft_and_nontext(window, monkeypatch):
    monkeypatch.setattr(document, 'ansi_encoding', lambda: 'cp1252')
    window.new_document('txt')
    window.editor.setPlainText('中文')
    window.editor.document().setModified(False)
    errors = []
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: errors.append(args))
    assert not window.convert_encoding('ansi')
    assert errors and window.current_session.encoding == 'utf-8'
    assert not window.editor.document().isModified()
    assert not window.reload_encoding('utf-8')
    for kind in ('image', 'pdf', 'diagram'):
        window.current_session.kind = kind
        window.update_status()
        assert not window.encoding_label.isEnabled()
        assert not window.convert_encoding('utf-8-sig')
        assert not window.reload_encoding('utf-8')
    window.current_session.kind = 'text'


def test_encoding_only_dirty_cross_file_workspace_save_and_close(window, tmp_path, monkeypatch):
    path = tmp_path / 'one.txt'
    path.write_bytes(b'one')
    assert window.open_document_path(path)
    first = window.current_session
    assert window.convert_encoding('utf-16-be')
    assert first.document.isModified() and not first.document.can_undo()
    window.new_document('txt')
    assert window.current_session.encoding == 'utf-8'
    assert window.flush_workspace()
    reopened = type(window)(settings=window.settings)
    try:
        restored = next(s for s in reopened.sessions if s.path == path.resolve())
        reopened._bind_document(restored)
        assert restored.encoding == 'utf-16-be' and restored.document.isModified()
        assert reopened.save_document()
        assert path.read_bytes() == codecs.BOM_UTF16_BE + 'one'.encode('utf-16-be')
        assert not restored.document.isModified()
    finally:
        for session in reopened.sessions:
            session.document.setModified(False)
        reopened.close()
    window._bind_document(first)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: QMessageBox.Cancel)
    assert not window.confirm_discard()
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: QMessageBox.Discard)
    assert window.confirm_discard()
    window.close_document_session(first)
    assert first not in window.sessions


def test_ansi_utf8_system_keeps_explicit_label(window, monkeypatch):
    monkeypatch.setattr(document, 'ansi_encoding', lambda: 'utf-8')
    window.new_document('txt')
    assert window.convert_encoding('ansi')
    assert window.current_session.encoding == 'ansi'
    assert 'ANSI' in window.encoding_label.text()
    assert window.current_session.document.isModified()
