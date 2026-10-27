import pymupdf as fitz
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QColorDialog, QMessageBox

from test_ui import app  # noqa: F401
from notepadview.pdf_editor import PdfEditor
from notepadview.pdf_model import PdfModel


@pytest.fixture
def pdf_editor(app):
    with fitz.open() as doc:
        page = doc.new_page(width=300, height=300)
        page.insert_text((40, 65), 'Original', fontsize=16)
        model = PdfModel(doc.tobytes())
        try:
            editor = PdfEditor(model.snapshot())
        finally:
            model.close()
    editor.resize(850, 700)
    editor.show()
    app.processEvents()
    yield editor
    editor.close()
    editor.deleteLater()
    app.processEvents()


def test_click_selects_without_preparing_drag(pdf_editor, monkeypatch):
    calls = []
    monkeypatch.setattr(pdf_editor, 'begin_drag', lambda: calls.append(True))
    point = pdf_editor.view.mapFromScene(QPointF(50, 60))
    QTest.mouseClick(pdf_editor.view.viewport(), Qt.LeftButton, pos=point)
    assert pdf_editor.selected == ('block', 0)
    assert calls == []


def test_failed_layer_size_restores_toolbar_and_selection(pdf_editor, monkeypatch):
    editor = pdf_editor
    editor.add_text('Added', 40, 180)
    before = editor.model.checkpoint()
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args))
    editor.size_box.setValue(300)
    assert warnings
    assert editor.model.checkpoint() == before
    assert editor.selected == ('layer', 0)
    assert editor.size_box.value() == editor.model.layers[0]['font_size'] == 16
    assert editor.text_font.pointSizeF() == 16


def test_failed_color_change_restores_actual_color(pdf_editor, monkeypatch):
    editor = pdf_editor
    editor.select_at(QPointF(50, 60))
    original = editor.fill
    monkeypatch.setattr(QColorDialog, 'getColor', lambda *args: QColor('#ff0000'))
    monkeypatch.setattr(editor.model, 'replace_text', lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('测试失败')))
    with pytest.raises(ValueError, match='测试失败'):
        editor.choose_color()
    assert editor.selected == ('block', 0)
    assert editor.fill == original


def test_double_click_blank_area_does_not_edit_previous_selection(pdf_editor, monkeypatch):
    calls = []
    pdf_editor.select_at(QPointF(50, 60))
    monkeypatch.setattr(pdf_editor, 'edit_selection', lambda: calls.append(True))
    point = pdf_editor.view.mapFromScene(QPointF(200, 240))
    QTest.mouseDClick(pdf_editor.view.viewport(), Qt.LeftButton, pos=point)
    QTest.qWait(1)
    assert pdf_editor.selected is None
    assert calls == []


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_escape_cancels_drag_without_mutating_document(pdf_editor, app, rotation):
    editor = pdf_editor
    editor.rotate_page(rotation)
    editor.add_text('Move', 40, 180)
    before = editor.model.checkpoint()
    count = editor.undo_stack.count()
    point = QPointF(*editor.model.to_display(0, (50, 190)))
    start = editor.view.mapFromScene(point)
    end = start + editor.view.mapFromScene(QPointF(30, 20)) - editor.view.mapFromScene(QPointF(0, 0))
    QTest.mousePress(editor.view.viewport(), Qt.LeftButton, pos=start)
    QTest.mouseMove(editor.view.viewport(), end)
    assert editor._drag_item is not None
    QTest.keyClick(editor.view, Qt.Key_Escape)
    QTest.mouseRelease(editor.view.viewport(), Qt.LeftButton, pos=end)
    app.processEvents()
    assert editor.model.checkpoint() == before
    assert editor.undo_stack.count() == count
    assert editor._drag_item is None


@pytest.mark.parametrize('kind', ['block', 'layer'])
def test_accepting_unchanged_text_keeps_history_clean(pdf_editor, monkeypatch, kind):
    editor = pdf_editor
    if kind == 'layer':
        editor.add_text('Added', 40, 180)
        text = 'Added'
    else:
        editor.select_at(QPointF(50, 60))
        text = 'Original'
    editor.undo_stack.setClean()
    count = editor.undo_stack.count()
    monkeypatch.setattr(editor, 'text_dialog', lambda *args: (text, True))
    editor.edit_selection()
    assert editor.undo_stack.count() == count
    assert editor.undo_stack.isClean()


@pytest.mark.parametrize('kind', ['block', 'layer'])
def test_choosing_unchanged_color_keeps_history_clean(pdf_editor, monkeypatch, kind):
    editor = pdf_editor
    if kind == 'layer':
        editor.add_text('Added', 40, 180)
    else:
        editor.select_at(QPointF(50, 60))
    editor.undo_stack.setClean()
    count = editor.undo_stack.count()
    monkeypatch.setattr(QColorDialog, 'getColor', lambda *args: QColor(editor.fill))
    editor.choose_color()
    assert editor.undo_stack.count() == count
    assert editor.undo_stack.isClean()


def test_choose_fill_color_also_updates_different_stroke(app, monkeypatch):
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 65), 'Outlined', fontsize=16, render_mode=2,
                         fill=(0, 0, 0), color=(1, 0, 0))
        model = PdfModel(doc.tobytes())
        try:
            editor = PdfEditor(model.snapshot())
        finally:
            model.close()
    try:
        editor.select_at(QPointF(50, 60))
        monkeypatch.setattr(QColorDialog, 'getColor', lambda *args: QColor('#000000'))
        editor.choose_color()
        strokes = [trace for trace in editor.model._doc[0].get_texttrace() if trace['type'] == 1]
        assert strokes
        assert all(trace['color'] == (0, 0, 0) for trace in strokes)
        assert editor.undo_stack.count() == 1
        editor.undo_stack.undo()
        assert any(trace['type'] == 1 and trace['color'] == (1, 0, 0)
                   for trace in editor.model._doc[0].get_texttrace())
    finally:
        editor.close()
        editor.deleteLater()


def test_accepting_same_fractional_stroke_color_keeps_clean_history(app, monkeypatch):
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 65), 'Outlined', fontsize=16, render_mode=2,
                         fill=(0.5, 0.5, 0.5), color=(0.5, 0.5, 0.5))
        model = PdfModel(doc.tobytes())
        try:
            editor = PdfEditor(model.snapshot())
        finally:
            model.close()
    try:
        editor.select_at(QPointF(50, 60))
        monkeypatch.setattr(QColorDialog, 'getColor', lambda *args: QColor(editor.fill))
        editor.choose_color()
        assert editor.undo_stack.count() == 0
        assert editor.undo_stack.isClean()
    finally:
        editor.close()
        editor.deleteLater()
