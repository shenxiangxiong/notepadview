from PySide6.QtWidgets import QFileDialog
from notepadview.dialogs import NewDocumentDialog
from notepadview.text_types import save_filter
from test_ui import app, window  # noqa: F401


def test_untyped_creation_and_save_choice(window, tmp_path, monkeypatch):
    dialog = NewDocumentDialog(window)
    try:
        assert 'untyped' in [dialog.type_list.item(i).data(256) for i in range(dialog.type_list.count())]
    finally:
        dialog.deleteLater()
    window.new_document('untyped')
    assert window.current_session.name == '未命名'
    assert window.current_session.document_format == 'txt'
    window.editor.insertPlainText('{"value": 1}')
    destination = tmp_path / 'chosen'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(destination), save_filter('json')))
    assert window.save_document()
    assert window.current_session.path == destination.with_suffix('.json')
    assert window.current_session.document_format == 'json'
    assert destination.with_suffix('.json').read_text() == '{"value": 1}'


def test_untyped_workspace_and_cancel(window, monkeypatch):
    window.new_document('untyped')
    window.editor.insertPlainText('draft')
    assert window.flush_workspace()
    restored = type(window)(settings=window.settings)
    try:
        assert restored.current_session.name == '未命名'
        assert restored.current_session.untyped
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: ('', ''))
        assert not restored.save_document()
        assert restored.current_session.untyped
        assert restored.editor.source_text() == 'draft'
        restored.current_session.document.setModified(False)
    finally:
        restored.close()
