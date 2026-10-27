from PySide6.QtWidgets import QMessageBox

from test_ui import app, window  # noqa: F401
from test_any_file import pdf_file


def test_legacy_pdf_edit_cache_restores_as_readonly(window, tmp_path, monkeypatch):
    path = pdf_file(tmp_path)
    assert window.open_document_path(path)
    state = window.workspace.snapshot()
    data = state['documents'][0]
    data['modified'] = True
    data['image_edit'] = {'version': 2, 'initial': {'data': 'old pdf'}, 'history': []}
    window.workspace.store.save(state)
    for asset in window.workspace.store.path.parent.glob(window.workspace.store.path.name + '.assets/*'):
        asset.unlink()
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: None)
    restored = type(window)(settings=window.settings)
    try:
        assert restored.workspace.load_error is None
        assert restored.current_session.kind == 'pdf'
        assert restored.current_session.image_editor is None
        assert not restored.current_session.document.isModified()
        assert restored.binary_preview.pdf_document.pageCount() == 2
        assert not restored.undo_action.isEnabled()
        assert not restored.redo_action.isEnabled()
        assert 'image_edit' not in restored.workspace.snapshot()['documents'][0]
    finally:
        restored.close()
