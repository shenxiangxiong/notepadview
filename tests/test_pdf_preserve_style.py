import pymupdf as fitz
import pytest
from notepadview.pdf_model import PdfModel
from test_ui import app  # noqa: F401


def test_enlarging_into_neighbor_rolls_back_only_current_change():
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 80), 'First', fontsize=16)
        page.insert_text((50, 110), 'Neighbor', fontsize=16)
        model = PdfModel(doc.tobytes())
    model.add_text(0, (300, 100, 500, 180), 'Keep this')
    before = model.checkpoint()
    with pytest.raises(ValueError, match='重叠'):
        model.replace_text(0, 0, 'First', size=64)
    assert model.checkpoint() == before
    assert model.layers[0]['text'] == 'Keep this'
    assert model.text_blocks(0)[0]['text'] == 'First'


def test_edit_preserves_mixed_span_styles():
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 80), 'Hello ', fontsize=16, fontname='hebo', color=(1, 0, 0))
        page.insert_text((100, 80), 'world', fontsize=12, fontname='tiit', color=(0, 0, 1))
        model = PdfModel(doc.tobytes())
    model.replace_text(0, 0, 'Hello earth')
    with model.open() as doc:
        spans = doc[0].get_text('dict')['blocks'][0]['lines'][0]['spans']
        assert spans[0]['font'] == 'Helvetica-Bold'
        assert spans[0]['color'] == 0xff0000
        assert spans[1]['font'] == 'Times-Italic'
        assert spans[1]['size'] == 12
        assert spans[1]['color'] == 0x0000ff
        assert spans[1]['text'] == 'earth'


def test_failed_resize_restores_controls_and_undo_history(app):
    from notepadview.pdf_editor import PdfEditor
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 80), 'First', fontsize=16)
        page.insert_text((50, 110), 'Neighbor', fontsize=16)
        editor = PdfEditor(PdfModel(doc.tobytes()).snapshot())
    editor.add_text('Keep this', 300, 100)
    editor.selected = ('block', 0)
    before = editor.model.checkpoint()
    count = editor.undo_stack.count()
    with pytest.raises(ValueError, match='重叠'):
        editor.set_font_style(size=64)
    assert editor.size_box.value() == 16
    assert editor.selected == ('block', 0)
    assert editor.model.checkpoint() == before
    assert editor.undo_stack.count() == count
    editor.undo_stack.undo()
    assert editor.model.layers == []
    editor.undo_stack.redo()
    assert editor.model.layers[0]['text'] == 'Keep this'
    editor.close()
    editor.deleteLater()
