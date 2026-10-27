from pathlib import Path

import pymupdf as fitz
import pytest

from notepadview.pdf_model import PdfModel


@pytest.mark.parametrize('filename', ['arial.ttf', 'calibri.ttf', 'msyh.ttc', 'simsun.ttc'])
def test_edit_embedded_font_with_different_resource_name(filename):
    path = Path(r'C:\Windows\Fonts') / filename
    if not path.exists():
        pytest.skip('Windows font unavailable')
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 80), 'Original text', fontname='custom', fontfile=str(path), fontsize=16)
        before = page.get_text('dict')['blocks'][0]['lines'][0]['spans'][0]
        model = PdfModel(doc.tobytes())
    model.replace_text(0, 0, 'Edited text')
    with fitz.open(stream=model.export(), filetype='pdf') as doc:
        span = doc[0].get_text('dict')['blocks'][0]['lines'][0]['spans'][0]
        assert span['text'].replace('\xa0', ' ') == 'Edited text'
        assert span['font'] == before['font']
        assert span['size'] == before['size']


def test_edit_latin_original_with_chinese_characters():
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 80), 'Original text', fontsize=16)
        model = PdfModel(doc.tobytes())
    model.replace_text(0, 0, '中文修改')
    assert model.text_blocks(0)[0]['text'] == '中文修改'
    restored = PdfModel.from_snapshot(model.snapshot())
    assert restored.text_blocks(0)[0]['text'] == '中文修改'


def test_edit_subset_font_missing_new_glyphs():
    path = Path(r'C:\Windows\Fonts\arial.ttf')
    if not path.exists():
        pytest.skip('Windows font unavailable')
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 80), 'Original text', fontname='custom', fontfile=str(path), fontsize=16)
        doc.subset_fonts()
        model = PdfModel(doc.tobytes())
    model.replace_text(0, 0, '中文修改')
    assert model.text_blocks(0)[0]['text'] == '中文修改'
