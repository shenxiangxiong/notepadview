import codecs
import ctypes
import os

import pytest
from PySide6.QtWidgets import QMessageBox

from notepadview import document
from test_ui import app, window  # noqa: F401


def test_windows_ansi_is_current_acp():
    if os.name != 'nt':
        pytest.skip('Windows ACP only')
    assert document.ansi_encoding() == 'cp' + str(ctypes.windll.kernel32.GetACP())


def test_forced_little_endian_without_bom_and_opposite_bom(tmp_path):
    path = tmp_path / 'note.txt'
    path.write_bytes('中文'.encode('utf-16-le'))
    assert document.load_text_document(path, encoding='utf-16')[0] == '中文'
    path.write_bytes(codecs.BOM_UTF16_BE + '中文'.encode('utf-16-be'))
    with pytest.raises(ValueError):
        document.load_text_document(path, encoding='utf-16')


@pytest.mark.parametrize('encoding', ['ansi', 'utf-8', 'utf-8-sig', 'utf-16-be', 'utf-16'])
def test_all_encoding_metadata_survives_sqlite(window, encoding):
    window.new_document('txt')
    assert window.convert_encoding(encoding)
    assert window.flush_workspace()
    state = window.workspace.store.load()
    assert state['documents'][0]['encoding'] == encoding
    assert state['documents'][0]['modified'] == (encoding != 'utf-8')


def test_reload_cancel_and_failure_keep_encoding_dirty_and_undo(window, tmp_path, monkeypatch):
    path = tmp_path / 'note.txt'
    path.write_bytes(b'text')
    assert window.open_document_path(path)
    window.editor.insertPlainText('edit')
    assert window.convert_encoding('utf-8-sig')
    before = window.editor.source_text()
    state = window.editor.selection_state()
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.No)
    assert not window.reload_encoding('utf-8')
    assert window.current_session.encoding == 'utf-8-sig'
    assert window.editor.source_text() == before
    assert window.editor.selection_state() == state
    path.write_bytes(b'\xff')
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: None)
    assert not window.reload_encoding('utf-8')
    window.editor.undo()
    assert window.editor.source_text() == 'text'
    assert window.editor.document().isModified()
