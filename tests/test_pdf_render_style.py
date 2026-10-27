from pathlib import Path
import pymupdf as fitz
import pytest
from notepadview.pdf_model import PdfModel


def test_content_edit_preserves_stroke_and_opacity():
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 80), 'Original text', fontsize=16, render_mode=2,
                         fill_opacity=0.5, stroke_opacity=0.5)
        model = PdfModel(doc.tobytes())
    before = model._doc[0].get_texttrace()
    model.replace_text(0, 0, 'Edited text')
    with fitz.open(stream=model.export(), filetype='pdf') as doc:
        after = doc[0].get_texttrace()
    assert len(after) == len(before)
    for old, new in zip(before, after):
        assert new['type'] == old['type']
        assert new['opacity'] == pytest.approx(old['opacity'])
        assert new['linewidth'] == pytest.approx(old['linewidth'])


def test_subset_bold_font_uses_full_original_font_for_new_letters():
    path = Path(r'C:\Windows\Fonts\arialbd.ttf')
    if not path.exists():
        pytest.skip('Windows font unavailable')
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 80), 'WWWWWWWW', fontname='custom', fontfile=str(path), fontsize=16)
        before = page.get_text('dict')['blocks'][0]['lines'][0]['spans'][0]
        doc.subset_fonts()
        model = PdfModel(doc.tobytes())
    model.replace_text(0, 0, 'Edited')
    with fitz.open(stream=model.export(), filetype='pdf') as doc:
        span = doc[0].get_text('dict')['blocks'][0]['lines'][0]['spans'][0]
        assert span['font'].split('+')[-1] == before['font'].split('+')[-1]
        assert span['flags'] & 16
