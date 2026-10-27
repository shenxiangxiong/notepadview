import pymupdf as fitz
import pytest
from test_ui import app  # noqa: F401
from notepadview.pdf_model import PdfModel
from notepadview.pdf_editor import PdfEditor


def test_original_text_can_increase_font_size(app):
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 80), 'Original text', fontsize=16)
        editor = PdfEditor(PdfModel(doc.tobytes()).snapshot())
    try:
        editor.selected = ('block', 0)
        editor.set_font_style(size=32)
        with editor.model.open() as doc:
            span = doc[0].get_text('dict')['blocks'][0]['lines'][0]['spans'][0]
            assert span['size'] == 32
            assert 'Original' in doc[0].get_text()
        editor.undo_stack.undo()
        with editor.model.open() as doc:
            assert doc[0].get_text('dict')['blocks'][0]['lines'][0]['spans'][0]['size'] == 16
    finally:
        editor.close()


def test_added_text_box_grows_for_larger_font():
    with fitz.open() as doc:
        doc.new_page()
        model = PdfModel(doc.tobytes())
    model.add_text(0, (30, 100, 290, 180), 'Added text', size=16)
    model.update_layer(0, font_size=80)
    assert model.layers[0]['font_size'] == 80
    assert model.layers[0]['rect'][3] > 180
    with fitz.open(stream=model.export(), filetype='pdf') as doc:
        assert 'Added' in doc[0].get_text()
        assert 'text' in doc[0].get_text()


def test_growth_beyond_page_rolls_back():
    with fitz.open() as doc:
        doc.new_page(width=100, height=100)
        model = PdfModel(doc.tobytes())
    model.add_text(0, (10, 10, 90, 50), 'Text', size=12)
    before = model.checkpoint()
    with pytest.raises(ValueError, match='页面可用区域'):
        model.update_layer(0, font_size=300)
    assert model.checkpoint() == before
