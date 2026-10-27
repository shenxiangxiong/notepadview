import pymupdf as fitz
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QColorDialog, QInputDialog

from test_ui import app  # noqa: F401
from test_pdf_editing import pdf_file


def test_double_click_original_and_new_color(app, tmp_path, monkeypatch):
    from notepadview.pdf_editor import PdfEditor
    editor = PdfEditor.from_path(pdf_file(tmp_path))
    editor.resize(700, 700)
    editor.show()
    app.processEvents()
    monkeypatch.setattr(PdfEditor, 'text_dialog', lambda *args: ('Double', True))
    point = editor.view.mapFromScene(QPointF(50, 60))
    QTest.mouseClick(editor.view.viewport(), Qt.LeftButton, pos=point)
    QTest.mouseDClick(editor.view.viewport(), Qt.LeftButton, pos=point)
    app.processEvents()
    assert editor.model.text_blocks(0)[0]['text'] == 'Double'
    with editor.model.open() as doc:
        span = doc[0].get_text('dict')['blocks'][0]['lines'][0]['spans'][0]
        assert span['font'] == 'Helvetica' and span['size'] == 16
        assert span['origin'] == pytest.approx((40, 65))
    editor.add_text('New', 40, 120)
    monkeypatch.setattr(QColorDialog, 'getColor', lambda *args: QColor('#123456'))
    editor.choose_color()
    assert editor.model.layers[0]['fill'] == '#123456'
    assert editor.model.layers[0]['font_size'] == 16
    editor.undo_stack.undo()
    assert editor.model.layers[0]['fill'] == '#000000'
    editor.close()
    editor.deleteLater()


def test_mixed_styles_color_preserves_origins_and_size():
    from notepadview.pdf_model import PdfModel
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 65), 'Hello ', fontsize=16, fontname='hebo')
        page.insert_text((85, 65), 'world', fontsize=12, fontname='tiit')
        model = PdfModel(doc.tobytes())
    with model.open() as doc:
        before = doc[0].get_text('dict')['blocks'][0]['lines'][0]['spans']
    block = model.text_blocks(0)[0]
    model.replace_text(0, block['id'], block['text'], fill='#abcdef')
    with model.open() as doc:
        after = doc[0].get_text('dict')['blocks'][0]['lines'][0]['spans']
    assert len(after) == len(before)
    for old, new in zip(before, after):
        assert new['font'] == old['font'] and new['size'] == old['size']
        assert new['origin'] == pytest.approx(old['origin'])
        assert new['color'] == 0xabcdef
    model.close()


def test_replacement_can_extend_beyond_original_line(tmp_path):
    from notepadview.pdf_model import PdfModel
    model = PdfModel.from_path(pdf_file(tmp_path))
    model.replace_text(0, 0, 'W' * 16)
    with model.open() as doc:
        assert 'W' * 16 in doc[0].get_text()
    model.close()


def test_multiline_original_edit_reuses_font():
    from notepadview.pdf_model import PdfModel
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 65), 'First\nSecond', fontsize=16, fontname='cour')
        model = PdfModel(doc.tobytes())
    model.replace_text(0, 0, 'One\nTwo')
    with model.open() as doc:
        spans = [s for b in doc[0].get_text('dict')['blocks'] for line in b['lines'] for s in line['spans']]
        assert all(s['font'] == 'Courier' and s['size'] == 16 for s in spans)
        assert 'One' in doc[0].get_text() and 'Two' in doc[0].get_text()
    model.close()


def test_default_replace_too_long_rolls_back(tmp_path):
    from notepadview.pdf_model import PdfModel
    model = PdfModel.from_path(pdf_file(tmp_path))
    before = model.data
    with pytest.raises(ValueError, match='超出'):
        model.replace_text(0, 0, 'long content ' * 100)
    assert model.data == before
    assert model.operations == []
    model.close()
