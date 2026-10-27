import pymupdf as fitz
import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QMessageBox

from test_ui import app, window  # noqa: F401


def pdf_file(tmp_path, rotation=0):
    path = tmp_path / 'source.pdf'
    with fitz.open() as doc:
        page = doc.new_page(width=360, height=480)
        page.draw_rect(fitz.Rect(25, 30, 250, 100), color=(0, 0, 1))
        page.insert_text((40, 65), 'Original text', fontsize=16)
        page.set_rotation(rotation)
        doc.new_page(width=300, height=300).insert_text((30, 50), 'Second page')
        doc.save(path)
    return path


def test_pdf_model_annotations_text_and_preserved_content(tmp_path):
    from notepadview.pdf_model import PdfModel
    model = PdfModel.from_path(pdf_file(tmp_path))
    model.add_text(0, (30, 120, 280, 180), 'Added text', '#ff0000', 'Arial', 20)
    for kind, rect in [('highlight', (38, 47, 140, 70)), ('underline', (38, 47, 140, 70)), ('rect', (20, 200, 150, 250))]:
        model.annotate(0, kind, rect, '#ffff00')
    output = model.export()
    with fitz.open(stream=output, filetype='pdf') as doc:
        assert 'Original text' in doc[0].get_text()
        assert 'Added text' in doc[0].get_text().replace('\xa0', ' ')
        assert len(list(doc[0].annots())) == 3
        assert len(doc[0].get_drawings()) >= 1
        pix = doc[0].get_pixmap()
        assert any(pix.samples[i] > pix.samples[i + 1] + 80 for i in range(0, len(pix.samples) - 2, 3))


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_replace_text_keeps_vectors_and_overlapping_image(tmp_path, rotation):
    from notepadview.pdf_model import PdfModel
    path = pdf_file(tmp_path, rotation)
    with fitz.open(path) as doc:
        page = doc[0]
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 10, 10), False)
        pix.clear_with(210)
        page.insert_image(fitz.Rect(30, 35, 230, 90), pixmap=pix, overlay=False)
        data = doc.tobytes()
    model = PdfModel(data)
    block = next(block for block in model.text_blocks(0) if 'Original' in block['text'])
    model.replace_text(0, block['id'], 'Changed', '#123456', 'Arial', 14)
    with fitz.open(stream=model.export(), filetype='pdf') as doc:
        assert doc[0].rotation == rotation
        assert 'Original' not in doc[0].get_text()
        assert 'Changed' in doc[0].get_text()
        assert len(doc[0].get_images()) == 1
        assert len(doc[0].get_drawings()) == 1


def test_pdf_page_operations_and_forms(tmp_path):
    from notepadview.pdf_model import PdfModel
    path = pdf_file(tmp_path)
    with fitz.open(path) as doc:
        for name, kind, value, rect in [('name', fitz.PDF_WIDGET_TYPE_TEXT, '', (20, 280, 180, 310)), ('agree', fitz.PDF_WIDGET_TYPE_CHECKBOX, 'Off', (20, 320, 40, 340)), ('choice', fitz.PDF_WIDGET_TYPE_COMBOBOX, 'A', (20, 350, 180, 380))]:
            widget = fitz.Widget()
            widget.field_name, widget.field_type = name, kind
            widget.rect, widget.field_value = fitz.Rect(rect), value
            if name == 'choice':
                widget.choice_values = ['A', 'B']
            doc[0].add_widget(widget)
        data = doc.tobytes()
    model = PdfModel(data)
    for field in model.fields(0):
        model.set_field(0, field['id'], {'name': 'User value', 'agree': True, 'choice': 'B'}[field['name']])
    model.add_text(0, (20, 180, 180, 220), 'Layer')
    model.rotate_page(0, 90)
    model.move_page(0, 1)
    model.insert_pages(1, path)
    assert model.page_count == 4
    model.delete_page(1)
    assert model.page_count == 3
    with fitz.open(stream=model.export(), filetype='pdf') as doc:
        assert doc[2].rotation == 90
        values = {w.field_name: w.field_value for w in doc[2].widgets()}
        assert values['name'] == 'User value'
        assert values['agree'] != 'Off'
        assert values['choice'] == 'B'
        assert 'Layer' in doc[2].get_text()
    single = PdfModel(fitz.open(stream=model.export(), filetype='pdf').convert_to_pdf(from_page=0, to_page=0))
    with pytest.raises(ValueError):
        single.delete_page(0)


def test_pdf_session_history_workspace_and_save(window, tmp_path, monkeypatch):
    path = pdf_file(tmp_path, 90)
    assert window.open_document_path(path)
    session = window.current_session
    editor = session.image_editor
    assert editor.image_format == 'pdf'
    assert window.save_action.isEnabled() and window.save_as_action.isEnabled()
    editor.add_text('Cached PDF', x=40, y=120)
    assert session.document.isModified()
    window.undo_current()
    assert not session.document.isModified()
    window.redo_current()
    displayed = editor.model.to_display(0, (45, 125))
    editor.select_at(QPointF(*displayed))
    editor.move_text(70, 140)
    assert editor.model.layers[0]['rect'][:2] == [70, 140]
    assert window.flush_workspace()
    path.unlink()
    restored = type(window)(settings=window.settings)
    try:
        session = restored.current_session
        assert session.kind == 'pdf' and session.document.isModified()
        restored.new_document('python')
        restored._bind_document(session)
        assert restored.undo_action.isEnabled()
        monkeypatch.setattr(QMessageBox, 'critical', lambda *args: QMessageBox.Ok)
        assert not restored.save_to(tmp_path / 'missing' / 'bad.pdf')
        destination = tmp_path / 'saved.pdf'
        assert restored.save_to(destination)
        assert not session.document.isModified()
        with fitz.open(destination) as doc:
            assert 'Cached PDF' in doc[0].get_text().replace('\xa0', ' ')
            assert 'Original text' in doc[0].get_text()
        restored.undo_current()
        assert session.document.isModified()
        restored.redo_current()
        assert not session.document.isModified()
    finally:
        for item in restored.sessions:
            item.document.setModified(False)
        restored.close()


def test_pdf_scan_and_rotation_coordinates(app, tmp_path):
    from notepadview.pdf_editor import PdfEditor
    editor = PdfEditor.from_path(pdf_file(tmp_path, 90))
    point = editor.model.to_page(0, (50, 100))
    assert editor.model.to_display(0, point) == pytest.approx((50, 100))
    editor.add_text('Rotated visible', x=40, y=120)
    assert editor.model.layers[0]['page'] == 0
    editor.rotate_page(90)
    editor.undo_stack.undo()
    assert editor.model.rotation(0) == 90
    with fitz.open() as doc:
        doc.new_page()
        scan = editor.model.__class__(doc.tobytes())
    assert scan.text_blocks(0) == []
