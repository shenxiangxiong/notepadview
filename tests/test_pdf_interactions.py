import pymupdf as fitz
import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QInputDialog

from test_ui import app  # noqa: F401
from test_pdf_editing import pdf_file


def test_rotated_drag_text_annotation_edit_and_undo(app, tmp_path, monkeypatch):
    from notepadview.pdf_editor import PdfEditor
    editor = PdfEditor.from_path(pdf_file(tmp_path, 90))
    editor.add_text('Drag text', 60, 180)
    assert editor.select_at(QPointF(250, 80))
    editor.gesture(QPointF(250, 80), QPointF(260, 100))
    assert editor.model.layers[0]['rect'][:2] == pytest.approx([80, 170])
    monkeypatch.setattr(PdfEditor, 'text_dialog', lambda *args: ('Edited', True))
    editor.edit_selection()
    assert editor.model.layers[0]['text'] == 'Edited'
    editor.undo_stack.undo()
    assert editor.model.layers[0]['text'] == 'Drag text'
    editor.change('legacy annotation', lambda: editor.model.annotate(0, 'highlight', (20, 60, 150, 100)))
    assert len(editor.annotations()) == 1
    editor.selected = ('annot', editor.annotations()[0]['id'])
    editor.delete_selection()
    assert len(editor.annotations()) == 0
    editor.undo_stack.undo()
    assert len(editor.annotations()) == 1
    editor.deleteLater()


def test_failed_replacement_keeps_original_and_unapplied_redacts(tmp_path):
    from notepadview.pdf_model import PdfModel
    model = PdfModel.from_path(pdf_file(tmp_path))
    block = model.text_blocks(0)[0]
    before = model.data
    with pytest.raises(ValueError, match='超出'):
        model.replace_text(0, block['id'], 'too long ' * 500, size=100)
    assert model.data == before
    with model.open() as doc:
        doc[0].add_redact_annot(fitz.Rect(20, 30, 250, 100))
        model.data = doc.tobytes()
    with pytest.raises(ValueError, match='删改标记'):
        model.replace_text(0, block['id'], 'short')
    assert 'Original' in model.text_blocks(0)[0]['text']


def test_ui_form_and_original_entry(app, tmp_path, monkeypatch):
    from notepadview.pdf_editor import PdfEditor
    path = pdf_file(tmp_path)
    with fitz.open(path) as doc:
        w = fitz.Widget()
        w.field_name = 'Name'
        w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        w.rect = fitz.Rect(30, 180, 250, 220)
        doc[0].add_widget(w)
        data = doc.tobytes()
    editor = PdfEditor(PdfModel_snapshot(data))
    editor.selected = ('field', editor.model.fields(0)[0]['id'])
    monkeypatch.setattr(QInputDialog, 'getMultiLineText', lambda *args: ('Filled', True))
    editor.edit_selection()
    assert editor.model.fields(0)[0]['value'] == 'Filled'
    assert editor.select_at(QPointF(50, 60))
    monkeypatch.setattr(PdfEditor, 'text_dialog', lambda *args: ('New', True))
    editor.edit_selection()
    assert 'New' in editor.model.text_blocks(0)[0]['text']
    editor.deleteLater()


def PdfModel_snapshot(data):
    from notepadview.pdf_model import PdfModel
    return PdfModel(data).snapshot()


def test_chinese_text_and_export_display_choices(tmp_path):
    from notepadview.pdf_model import PdfModel
    with fitz.open() as doc:
        page = doc.new_page()
        widget = fitz.Widget()
        widget.field_name = 'Choice'
        widget.field_type = fitz.PDF_WIDGET_TYPE_COMBOBOX
        widget.rect = fitz.Rect(20, 200, 200, 230)
        widget.choice_values = [('one', 'First'), ('two', 'Second')]
        widget.field_value = 'one'
        page.add_widget(widget)
        model = PdfModel(doc.tobytes())
    model.add_text(0, (20, 40, 300, 120), '中文内容', family='Missing font', size=18)
    model.set_field(0, model.fields(0)[0]['id'], 'Second')
    with fitz.open(stream=model.export(), filetype='pdf') as result:
        assert '中文内容' in result[0].get_text()
        assert next(result[0].widgets()).field_value == 'two'
