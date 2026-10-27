import pymupdf as fitz
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from test_ui import app  # noqa: F401
from notepadview.pdf_editor import PdfEditor
from notepadview.pdf_model import PdfModel


@pytest.mark.parametrize('rotation', [0, 90])
def test_drag_follows_pointer_without_rerendering_or_history_writes(app, monkeypatch, rotation):
    with fitz.open() as doc:
        page = doc.new_page(width=500, height=600)
        page.set_rotation(rotation)
        editor = PdfEditor(PdfModel(doc.tobytes()).snapshot())
    editor.resize(900, 800)
    editor.show()
    editor.add_text('Drag me', 60, 100)
    app.processEvents()
    layer = editor.model.layers[0].copy()
    x, y = editor.model.to_display(0, (80, 120))
    start = editor.view.mapFromScene(QPointF(x, y))
    QTest.mousePress(editor.view.viewport(), Qt.LeftButton, pos=start)
    before = editor.undo_stack.count()
    assert getattr(editor, '_drag_item', None) is None
    # Crossing the drag threshold prepares the picture once; subsequent moves reuse it.
    QTest.mouseMove(editor.view.viewport(), start + editor.view.mapFromScene(QPointF(15, 10)) - editor.view.mapFromScene(QPointF(0, 0)))
    assert editor._drag_item is not None
    def unexpected_render(*args, **kwargs):
        pytest.fail('Dragging must not render PDF pages')
    original = editor.model.render
    monkeypatch.setattr(editor.model, 'render', unexpected_render)
    QTest.mouseMove(editor.view.viewport(), start + editor.view.mapFromScene(QPointF(30, 20)) - editor.view.mapFromScene(QPointF(0, 0)))
    app.processEvents()
    assert editor._drag_item.pos().manhattanLength() > 0
    assert editor.model.layers[0] == layer
    assert editor.undo_stack.count() == before
    monkeypatch.setattr(editor.model, 'render', original)
    QTest.mouseRelease(editor.view.viewport(), Qt.LeftButton, pos=start + editor.view.mapFromScene(QPointF(30, 20)) - editor.view.mapFromScene(QPointF(0, 0)))
    app.processEvents()
    assert editor.undo_stack.count() == before + 1
    assert editor.model.layers[0]['rect'] != layer['rect']
    editor.undo_stack.undo()
    assert editor.model.layers[0] == layer
    editor.close()
    editor.deleteLater()
