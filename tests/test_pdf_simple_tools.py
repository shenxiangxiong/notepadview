import json

import pymupdf as fitz
import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QInputDialog, QPushButton, QComboBox, QWidget

from test_ui import app  # noqa: F401
from test_pdf_editing import pdf_file


def test_four_tools_and_automatic_selection(app, tmp_path, monkeypatch):
    from notepadview.pdf_editor import PdfEditor
    editor = PdfEditor.from_path(pdf_file(tmp_path))
    panel = editor.findChild(QWidget, 'pdfToolsPanel')
    assert [b.text() for b in panel.findChildren(QPushButton)] == ['编辑文字', '新增文字', '删除文字', '颜色']
    assert panel.findChildren(QComboBox) == [editor.font_box]
    assert editor.size_box.minimum() == 1 and editor.size_box.maximum() == 300
    assert not editor.edit_button.isEnabled() and not editor.delete_button.isEnabled()
    assert editor.select_at(QPointF(50, 60)) and editor.selected[0] == 'block'
    monkeypatch.setattr(PdfEditor, 'text_dialog', lambda *args: ('Added', True))
    editor.add_button.click()
    assert editor.selected == ('layer', 0)
    layer = editor.model.layers[0]
    point = QPointF(layer['rect'][0] + 2, layer['rect'][1] + 2)
    assert editor.select_at(point) and editor.selected == ('layer', 0)
    editor.gesture(point, point + QPointF(10, 10))
    assert editor.model.layers[0]['rect'][0] == layer['rect'][0] + 10
    editor.delete_button.click()
    assert editor.model.layers == [] and editor.selected is None
    assert not editor.delete_button.isEnabled()
    editor.select_at(QPointF(50, 60))
    monkeypatch.setattr(PdfEditor, 'text_dialog', lambda *args: ('New', True))
    editor.edit_button.click()
    assert editor.model.text_blocks(0)[0]['text'] == 'New'
    editor.deleteLater()


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_original_color_delete_preserve_art_and_history(app, tmp_path, monkeypatch, rotation):
    from notepadview.pdf_editor import PdfEditor
    from notepadview.pdf_model import PdfModel
    with fitz.open(pdf_file(tmp_path, rotation)) as doc:
        page = doc[0]
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 10, 10), False)
        pix.clear_with(180)
        page.insert_image(fitz.Rect(30, 35, 230, 90), pixmap=pix, overlay=False)
        editor = PdfEditor(PdfModel(doc.tobytes()).snapshot())
    with editor.model.open() as doc:
        before = doc[0].get_text('dict')['blocks'][1 if doc[0].get_text('dict')['blocks'][0]['type'] == 1 else 0]['lines'][0]['spans'][0]
        drawings = doc[0].get_drawings()
        image = doc.extract_image(doc[0].get_images()[0][0])['image']
    def forbidden(*args, **kwargs):
        raise AssertionError('interaction serialized the whole PDF')
    monkeypatch.setattr(PdfModel, 'export', forbidden)
    monkeypatch.setattr(PdfModel, 'snapshot', forbidden)
    monkeypatch.setattr(PdfModel, 'data', property(forbidden))
    assert editor.select_at(QPointF(*editor.model.to_display(0, (50, 60))))
    monkeypatch.setattr(QColorDialog, 'getColor', lambda *args: QColor('#ff0000'))
    editor.choose_color()
    assert editor.selected is not None
    with editor.model.open() as doc:
        after = next(b for b in doc[0].get_text('dict')['blocks'] if b['type'] == 0)['lines'][0]['spans'][0]
        assert after['color'] == 0xff0000
        assert after['font'] == before['font'] and after['size'] == before['size']
        assert after['origin'] == pytest.approx(before['origin'])
    editor.delete_selection()
    assert editor.model.text_blocks(0) == []
    with editor.model.open() as doc:
        assert doc[0].get_drawings() == drawings
        assert doc.extract_image(doc[0].get_images()[0][0])['image'] == image
    state = json.loads(json.dumps(editor.snapshot()))
    restored = PdfEditor.from_snapshot(state)
    restored.undo_stack.undo()
    assert restored.model.text_blocks(0)[0]['text'] == 'Original text'
    restored.undo_stack.undo()
    with restored.model.open() as doc:
        span = next(b for b in doc[0].get_text('dict')['blocks'] if b['type'] == 0)['lines'][0]['spans'][0]
        assert span['color'] == before['color']
    restored.undo_stack.redo()
    restored.undo_stack.redo()
    monkeypatch.undo()
    destination = tmp_path / 'saved.pdf'
    restored.save(destination)
    with fitz.open(destination) as doc:
        assert not doc[0].get_text().strip()
        assert len(doc[0].get_images()) == len(doc[0].get_drawings()) == 1
    restored.deleteLater()
    editor.deleteLater()


def test_scan_hint_and_new_text(app, monkeypatch):
    from notepadview.pdf_editor import PdfEditor
    from notepadview.pdf_model import PdfModel
    with fitz.open() as doc:
        doc.new_page(width=120, height=120)
        editor = PdfEditor(PdfModel(doc.tobytes()).snapshot())
    assert not editor.select_at(QPointF(20, 20))
    assert 'OCR' in editor.hint.text()
    monkeypatch.setattr(PdfEditor, 'text_dialog', lambda *args: ('Hi', True))
    editor.add_button.click()
    assert editor.model.layers[0]['text'] == 'Hi'
    editor.deleteLater()
