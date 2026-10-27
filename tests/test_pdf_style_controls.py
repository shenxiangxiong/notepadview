import pymupdf as fitz
from PySide6.QtCore import QPointF
from PySide6.QtGui import QFont
from test_ui import app  # noqa: F401
from notepadview.pdf_editor import PdfEditor
from notepadview.pdf_model import PdfModel


def test_toolbar_styles_new_and_original_text(app):
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 80), 'Original text', fontsize=16)
        editor = PdfEditor(PdfModel(doc.tobytes()).snapshot())
    try:
        editor.font_box.setCurrentFont(QFont('Courier New'))
        editor.size_box.setValue(12)
        editor.add_text('Added', 50, 150)
        assert editor.model.layers[0]['font_size'] == 12
        editor.size_box.setValue(14)
        assert editor.model.layers[0]['font_size'] == 14
        editor.undo_stack.undo()
        assert editor.model.layers[0]['font_size'] == 12
        editor.select_at(QPointF(60, 75))
        assert editor.size_box.value() == 16
        editor.size_box.setValue(12)
        with editor.model.open() as doc:
            span = doc[0].get_text('dict')['blocks'][0]['lines'][0]['spans'][0]
            assert span['size'] == 12
        editor.fill = '#ff0000'
        editor.set_style()
        with editor.model.open() as doc:
            assert doc[0].get_text('dict')['blocks'][0]['lines'][0]['spans'][0]['color'] == 0xff0000
    finally:
        editor.close()
