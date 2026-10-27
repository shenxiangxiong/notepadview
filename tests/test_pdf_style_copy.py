import pymupdf as fitz
from PySide6.QtCore import QPointF
from test_ui import app  # noqa: F401
from notepadview.pdf_editor import PdfEditor
from notepadview.pdf_model import PdfModel


def test_copy_apply_text_style_is_one_undo_and_preserves_content(app):
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 80), 'Source style', fontsize=18, color=(1, 0, 0))
        editor = PdfEditor(PdfModel(doc.tobytes()).snapshot())
    editor.select_at(QPointF(60, 75))
    editor.copy_text_style()
    editor.add_text('Target', 50, 150)
    editor.model.update_layer(0, font_size=12, fill='#0000ff')
    editor._refresh()
    before = editor.model.layers[0].copy()
    count = editor.undo_stack.count()
    editor.apply_text_style()
    assert editor.model.layers[0]['text'] == 'Target'
    assert editor.model.layers[0]['font_size'] == 18
    assert editor.model.layers[0]['fill'] == '#ff0000'
    assert editor.undo_stack.count() == count + 1
    editor.undo_stack.undo()
    assert editor.model.layers[0] == before
    editor.undo_stack.redo()
    assert editor.model.layers[0]['fill'] == '#ff0000'
    editor.close()


def test_copy_pdf_bold_style_to_added_text_keeps_bold(app):
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 80), 'Bold source', fontname='hebo', fontsize=18)
        editor = PdfEditor(PdfModel(doc.tobytes()).snapshot())
    editor.select_at(QPointF(60, 75))
    editor.copy_text_style()
    editor.add_text('Target', 50, 150)
    editor.apply_text_style()
    with fitz.open(stream=editor.model.export(), filetype='pdf') as doc:
        spans = [s for b in doc[0].get_text('dict')['blocks'] for line in b['lines'] for s in line['spans']]
        assert next(s for s in spans if s['text'] == 'Target')['flags'] & 16
    editor.close()


def test_apply_style_to_original_preserves_text_and_can_undo(app):
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 80), 'Original', fontsize=12)
        editor = PdfEditor(PdfModel(doc.tobytes()).snapshot())
    editor.add_text('Source', 50, 150)
    editor.model.update_layer(0, font_size=18, fill='#ff0000')
    editor.copy_text_style()
    editor.select_at(QPointF(60, 75))
    count = editor.undo_stack.count()
    editor.apply_text_style()
    with editor.model.open() as doc:
        span = doc[0].get_text('dict')['blocks'][0]['lines'][0]['spans'][0]
        assert span['text'] == 'Original'
        assert span['size'] == 18
        assert span['color'] == 0xff0000
    assert editor.undo_stack.count() == count + 1
    editor.undo_stack.undo()
    with editor.model.open() as doc:
        assert doc[0].get_text('dict')['blocks'][0]['lines'][0]['spans'][0]['size'] == 12
    editor.close()


def test_style_menu_requires_selection_and_copied_style(app):
    with fitz.open() as doc:
        doc.new_page()
        editor = PdfEditor(PdfModel(doc.tobytes()).snapshot())
    assert not editor.style_menu().actions()[0].isEnabled()
    assert not editor.style_menu().actions()[1].isEnabled()
    editor.add_text('Example')
    menu = editor.style_menu()
    assert [a.text() for a in menu.actions()] == ['复制文字样式', '应用文字样式']
    assert menu.actions()[0].isEnabled()
    assert not menu.actions()[1].isEnabled()
    menu.actions()[0].trigger()
    assert editor.style_menu().actions()[1].isEnabled()
    editor.close()
