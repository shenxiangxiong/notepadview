from PySide6.QtWidgets import QMessageBox
from test_ui import app, window


def test_language_menu_changes_extension_and_preserves_unsaved_text(window, tmp_path):
    path = tmp_path / 'notes.txt'
    path.write_text('original', encoding='utf-8')
    assert window.open_document_path(path)
    window.editor.selectAll()
    window.editor.insertPlainText('unsaved edits')
    assert window.current_session.document.isModified()
    window.type_actions['json'].trigger()
    target = tmp_path / 'notes.json'
    assert window.current_session.path == target
    assert not path.exists()
    assert target.read_text(encoding='utf-8') == 'original'
    assert window.editor.source_text() == 'unsaved edits'
    assert window.current_session.document.isModified()
    assert window.editor.language == 'json'
    assert 'JSON' in window.language_button.text()
    assert window.save_document()
    assert target.read_text(encoding='utf-8') == 'unsaved edits'


def test_language_change_refuses_existing_destination(window, tmp_path, monkeypatch):
    path = tmp_path / 'notes.txt'
    path.write_text('original', encoding='utf-8')
    target = tmp_path / 'notes.json'
    target.write_text('keep', encoding='utf-8')
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: QMessageBox.Ok)
    assert window.open_document_path(path)
    window.type_actions['json'].trigger()
    assert window.current_session.path == path
    assert window.editor.language == 'txt'
    assert target.read_text(encoding='utf-8') == 'keep'
    assert window.type_actions['txt'].isChecked()


def test_language_change_updates_named_draft(window):
    window.new_document('txt')
    assert window.rename_document_session(window.current_session, 'example.txt')
    window.type_actions['python'].trigger()
    assert window.current_session.path is None
    assert window.current_session.name == 'example.py'
    assert window.editor.language == 'python'
    assert window.language_button.menu().actions() == list(window.type_actions.values())


def test_language_button_disabled_for_diagram(window):
    window.new_diagram('mindmap')
    assert not window.language_button.isEnabled()
