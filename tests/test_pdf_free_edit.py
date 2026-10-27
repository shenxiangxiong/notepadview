import pymupdf as fitz
import pytest
from PySide6.QtCore import QPointF
from test_ui import app  # noqa: F401
from notepadview.pdf_editor import PdfEditor
from notepadview.pdf_model import PdfModel


def make_model():
    with fitz.open() as doc:
        page = doc.new_page(width=500, height=600)
        page.insert_text((50, 80), 'Short', fontsize=16, fontname='hebo', color=(1, 0, 0))
        page.insert_text((50, 150), 'Neighbor', fontsize=12)
        return PdfModel(doc.tobytes())


def test_overlapping_originals_move_independently():
    model = make_model()
    model.replace_text(0, 0, 'Short', offset=(0, 70))
    block = next(b for b in model.text_blocks(0) if b['text'] == 'Short')
    model.replace_text(0, block['id'], 'Short', offset=(120, 0))
    with model.open() as doc:
        spans = [s for b in doc[0].get_text('dict')['blocks'] for l in b['lines'] for s in l['spans']]
        neighbor = next(s for s in spans if s['text'] == 'Neighbor')
        moved = next(s for s in spans if s['text'] == 'Short')
        assert tuple(neighbor['origin']) == (50, 150)
        assert tuple(moved['origin']) == (170, 150)
    neighbor_block = next(b for b in model.text_blocks(0) if b['text'] == 'Neighbor')
    model.replace_text(0, neighbor_block['id'], 'Neighbor', offset=(0, 100))
    with model.open() as doc:
        assert doc[0].get_text().count('Neighbor') == 1
        assert doc[0].get_text().count('Short') == 1
    model.close()


def test_overlap_drag_preview_contains_only_selected_text(app):
    model = make_model()
    model.replace_text(0, 0, 'Short', offset=(0, 70))
    editor = PdfEditor(model.snapshot())
    point = QPointF(*editor.model.to_display(0, (60, 145)))
    editor.select_at(point)
    before = editor.model.data
    editor.begin_drag()
    assert editor.model.data == before
    image = editor._drag_item.pixmap().toImage()
    visible = [image.pixelColor(x, y) for y in range(image.height()) for x in range(image.width())
               if image.pixelColor(x, y).alpha() > 50]
    assert visible
    assert all(c.red() > c.green() + 50 for c in visible)
    editor.gesture(point, point + QPointF(100, 0))
    with editor.model.open() as doc:
        assert 'Short' in doc[0].get_text() and 'Neighbor' in doc[0].get_text()
    editor.undo_stack.undo()
    assert len(editor.model.text_blocks(0)) == 2
    editor.close()
    model.close()


def test_edit_can_extend_and_add_lines_preserving_style():
    model = make_model()
    model.replace_text(0, 0, 'A much longer replacement\nAnother line')
    with model.open() as doc:
        assert 'A much longer replacement' in doc[0].get_text()
        spans = [s for b in doc[0].get_text('dict')['blocks'] for l in b['lines'] for s in l['spans'] if s['text'] != 'Neighbor']
        assert all(s['size'] == 16 and s['color'] == 0xff0000 and s['flags'] & 16 for s in spans)
    model.close()


@pytest.mark.parametrize('rotation', [0, 90])
def test_original_text_can_move_and_undo_with_style(app, rotation):
    model = make_model()
    model._doc[0].set_rotation(rotation)
    editor = PdfEditor(model.snapshot())
    point = editor.model.to_display(0, (60, 75))
    editor.select_at(QPointF(*point))
    before = editor.undo_stack.count()
    editor.begin_drag()
    assert editor._drag_item is not None
    editor.gesture(QPointF(*point), QPointF(point[0] + 20, point[1] + 10))
    assert editor.undo_stack.count() == before + 1
    with editor.model.open() as doc:
        spans = [s for b in doc[0].get_text('dict')['blocks'] for l in b['lines'] for s in l['spans']]
        span = next(s for s in spans if s['text'] == 'Short')
        assert tuple(span['origin']) != (50, 80)
        assert span['color'] == 0xff0000 and span['flags'] & 16
        assert 'Neighbor' in doc[0].get_text()
    editor.undo_stack.undo()
    with editor.model.open() as doc:
        span = doc[0].get_text('dict')['blocks'][0]['lines'][0]['spans'][0]
        assert tuple(span['origin']) == (50, 80)
    editor.close()
