import pytest
from PySide6.QtWidgets import QMessageBox, QTableWidget
from notepadview.office_editor import OfficeEditor
from notepadview.dialogs import NewDocumentDialog
from test_ui import app, window  # noqa: F401


@pytest.mark.parametrize('language', ['docx', 'xlsx'])
def test_office_view_only_open_restore(window, tmp_path, language):
    path = tmp_path / ('document.' + language)
    source = OfficeEditor.blank(language)
    source.save(path)
    original = path.read_bytes()
    assert window.open_document_path(path)
    session = window.current_session
    viewer = session.image_editor
    assert viewer.view_only
    assert not window.save_action.isEnabled()
    assert not window.save_as_action.isEnabled()
    assert not window.undo_action.isEnabled()
    assert not window.redo_action.isEnabled()
    assert not window.save_to(tmp_path / ('copy.' + language))
    assert not window.save_as()
    with pytest.raises(ValueError):
        viewer.save(path)
    viewer.edit_action('cut')
    viewer.edit_action('paste')
    assert not session.document.isModified()
    if language == 'docx':
        assert viewer.text_edit.isReadOnly()
        assert viewer.toolbar.isHidden()
    else:
        assert viewer.table.editTriggers() == QTableWidget.NoEditTriggers
        assert viewer.sheet_selector.isEnabled()
    assert window.flush_workspace()
    restored = type(window)(settings=window.settings)
    try:
        assert restored.workspace.load_error is None
        assert restored.current_session.image_editor.view_only
        assert not restored.current_session.document.isModified()
        assert not restored.save_action.isEnabled()
    finally:
        restored.close()
    assert path.read_bytes() == original


@pytest.mark.parametrize('language', [
    'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'odt', 'ods', 'odp', 'rtf',
])
def test_office_new_not_available(window, language):
    previous = window.current_session
    count = len(window.sessions)
    assert not window.new_document(language)
    assert len(window.sessions) == count
    assert window.current_session is previous
    assert language not in dict(NewDocumentDialog.FORMATS)


def test_invalid_office_open_keeps_existing(window, tmp_path, monkeypatch):
    window.new_document('txt')
    session = window.current_session
    path = tmp_path / 'invalid.docx'
    path.write_bytes(b'PK broken')
    monkeypatch.setattr(QMessageBox, 'critical', lambda *a: QMessageBox.Ok)
    assert not window.open_document_path(path)
    assert window.current_session is session


@pytest.mark.parametrize('suffix', [
    '.doc', '.xls', '.ppt', '.pptx', '.odt', '.ods', '.odp', '.rtf',
])
def test_legacy_office_formats_open_as_readonly_sessions(
        window, tmp_path, monkeypatch, suffix):
    jobs = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: QMessageBox.Ok)
    monkeypatch.setattr(
        window, '_start_reader_job',
        lambda session, operation: jobs.append((session, operation)),
        raising=False,
    )
    path = tmp_path / ('sample' + suffix)
    path.write_bytes(b'\x00office sample')

    assert window.open_document_path(path)
    session = window.current_session
    assert session.kind == 'office_pdf'
    assert session.path == path.resolve()
    assert window.editor.readOnly()
    assert not window.save_action.isEnabled()
    assert not window.save_as_action.isEnabled()
    assert not window.undo_action.isEnabled()
    assert not window.redo_action.isEnabled()
    assert not window.save_to(tmp_path / ('copy' + suffix))
    assert path.read_bytes() == b'\x00office sample'
    assert len(jobs) == 1 and jobs[0][0] is session
