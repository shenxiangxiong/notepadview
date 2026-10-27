import pymupdf as fitz
from PySide6.QtCore import QPointF, QTimer
from PySide6.QtWidgets import QInputDialog, QPlainTextEdit
from test_ui import app  # noqa: F401
from notepadview.pdf_editor import PdfEditor
from notepadview.pdf_model import PdfModel


def test_original_selection_keeps_color_for_new_text(app):
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 80), 'Original', fontsize=18, color=(1, 0, 0))
        editor = PdfEditor(PdfModel(doc.tobytes()).snapshot())
    editor.select_at(QPointF(60, 75))
    editor.add_text('Added', 50, 150)
    assert editor.model.layers[-1]['fill'] == '#ff0000'
    assert editor.model.layers[-1]['font_size'] == 18
    editor.close()


def test_edit_added_text_keeps_font_size_and_color(app, monkeypatch):
    with fitz.open() as doc:
        doc.new_page()
        editor = PdfEditor(PdfModel(doc.tobytes()).snapshot())
    editor.text_font.setFamily('Arial')
    editor.text_font.setPointSizeF(23)
    editor.fill = '#cc3300'
    editor.add_text('Before')
    before = editor.model.layers[0].copy()
    monkeypatch.setattr(editor, 'text_dialog', lambda *args: ('After', True))
    editor.edit_selection()
    after = editor.model.layers[0]
    assert after['text'] == 'After'
    for key in ('font_family', 'font_size', 'fill'):
        assert after[key] == before[key]
    editor.undo_stack.undo()
    assert editor.model.layers[0] == before
    editor.close()


def test_text_dialog_is_large_and_uses_current_style(app):
    with fitz.open() as doc:
        doc.new_page()
        editor = PdfEditor(PdfModel(doc.tobytes()).snapshot())
    editor.text_font.setFamily('Arial')
    editor.text_font.setPointSizeF(22)
    editor.fill = '#cc3300'
    observed = {}
    def inspect():
        dialog = editor.findChild(QInputDialog)
        observed['size'] = (dialog.width(), dialog.height())
        field = dialog.findChild(QPlainTextEdit)
        observed['font'] = field.font().pointSizeF()
        observed['color'] = field.palette().color(field.foregroundRole()).name()
        dialog.setTextValue('Changed')
        dialog.accept()
    QTimer.singleShot(0, inspect)
    assert editor.text_dialog('修改文字', '内容', 'Original') == ('Changed', True)
    assert observed['size'][0] >= 640
    assert observed['size'][1] >= 400
    assert observed['font'] == 22
    assert observed['color'] != '#cc3300'
    editor.close()
