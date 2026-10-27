import json
import pymupdf as fitz
from PySide6.QtCore import QPointF
from test_ui import app  # noqa: F401
from test_pdf_editing import pdf_file


def test_interactions_never_export_pdf(app, tmp_path, monkeypatch):
    from notepadview.pdf_editor import PdfEditor
    from notepadview.pdf_model import PdfModel
    editor = PdfEditor.from_path(pdf_file(tmp_path))
    def forbidden(*args, **kwargs):
        raise AssertionError('interaction exported entire PDF')
    monkeypatch.setattr(PdfModel, 'export', forbidden)
    editor.go_page(1)
    editor.select_at(QPointF(5, 5))
    editor.zoom(1.2)
    editor.add_text('Small edit', 20, 100)
    editor.rotate_page(90)
    editor.undo_stack.undo()
    editor.undo_stack.redo()
    assert editor.model.rotation(1) == 90


def test_history_stores_source_once_and_restores_branch(app, tmp_path):
    from notepadview.pdf_editor import PdfEditor
    editor = PdfEditor.from_path(pdf_file(tmp_path))
    base_size = len(json.dumps(editor.snapshot()))
    for i in range(5):
        editor.add_text(str(i), 20, 100)
        editor.rotate_page(90)
    state = editor.snapshot()
    assert len(json.dumps(state)) < base_size + 20000
    editor.undo_stack.undo()
    state = editor.snapshot()
    restored = PdfEditor.from_snapshot(state)
    assert restored.model.rotation(0) == editor.model.rotation(0)
    restored.undo_stack.redo()
    assert restored.model.rotation(0) == 90
    restored.undo_stack.undo()
    restored.add_text('Branch', 20, 150)
    assert not restored.undo_stack.canRedo()
    with fitz.open(stream=restored.model.export(), filetype='pdf') as doc:
        assert 'Branch' in doc[0].get_text()
        assert 'Original' in doc[0].get_text()


def test_selection_zoom_reuses_render(app, tmp_path, monkeypatch):
    from notepadview.pdf_editor import PdfEditor
    editor = PdfEditor.from_path(pdf_file(tmp_path))
    original = editor.model.render
    calls = []
    def render(*args):
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(editor.model, 'render', render)
    editor.select_at(QPointF(1, 1))
    editor.zoom(1.2)
    assert not calls


def test_mixed_operations_restart_and_undo(app, tmp_path):
    import base64
    from notepadview.pdf_editor import PdfEditor
    editor = PdfEditor.from_path(pdf_file(tmp_path))
    block = editor.model.text_blocks(0)[0]
    editor.change('replace', lambda: editor.model.replace_text(0, block['id'], 'Changed', size=12))
    editor.change('annotate', lambda: editor.model.annotate(0, 'rect', (20, 200, 70, 240)))
    source = base64.b64encode(pdf_file(tmp_path).read_bytes()).decode('ascii')
    editor.change('insert', lambda: editor.model.insert_data(1, source))
    editor.change('move', lambda: editor.model.move_page(0, 3))
    editor.change('delete', lambda: editor.model.delete_page(1))
    restored = PdfEditor.from_snapshot(json.loads(json.dumps(editor.snapshot())))
    assert restored.model.page_count == 3
    for _ in range(5):
        restored.undo_stack.undo()
    assert restored.model.page_count == 2
    assert 'Original' in restored.model.text_blocks(0)[0]['text']
    for _ in range(5):
        restored.undo_stack.redo()
    with fitz.open(stream=restored.model.export(), filetype='pdf') as doc:
        assert 'Changed' in doc[2].get_text()
        assert len(list(doc[2].annots())) == 1


def test_render_is_bounded_and_original_selection_omits_image_data(tmp_path):
    from notepadview.pdf_model import PdfModel
    with fitz.open() as doc:
        page = doc.new_page(width=20000, height=20000)
        page.insert_text((40, 50), 'Huge page')
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), False)
        pix.clear_with(200)
        page.insert_image(fitz.Rect(100, 100, 500, 500), pixmap=pix)
        model = PdfModel(doc.tobytes())
    rendered, _, _ = model.render(0)
    assert max(rendered.width, rendered.height) <= 2000
    assert rendered.width * rendered.height <= 4000000
    assert model.text_blocks(0)[0]['text'] == 'Huge page'
    model.close()


def test_legacy_history_migrates_without_snapshotting_new_edits(app, tmp_path, monkeypatch):
    from notepadview.pdf_editor import PdfEditor
    from notepadview.pdf_model import PdfModel
    model = PdfModel.from_path(pdf_file(tmp_path))
    before = model.snapshot()
    model.add_text(0, (30, 120, 280, 180), 'Legacy')
    after = model.snapshot()
    model.close()
    editor = PdfEditor.from_snapshot({'initial': before, 'history': [dict(before=before, after=after, label='old')], 'index': 1})
    monkeypatch.setattr(PdfModel, 'snapshot', lambda *_: (_ for _ in ()).throw(AssertionError('full snapshot')))
    editor.add_text('New', 30, 200)
    editor.undo_stack.undo()
    editor.undo_stack.undo()
    assert editor.model.layers == []
    editor.undo_stack.redo()
    assert editor.model.layers[0]['text'] == 'Legacy'
    restored = PdfEditor.from_snapshot(editor.snapshot())
    assert restored.model.layers[0]['text'] == 'Legacy'


def test_insert_path_history_embeds_content(tmp_path):
    from notepadview.pdf_model import PdfModel
    path = pdf_file(tmp_path)
    model = PdfModel.from_path(path)
    before = model.checkpoint()
    model.insert_pages(1, path)
    after = model.checkpoint()
    path.unlink()
    assert model.operations[0]['method'] == 'insert_data'
    model.restore_checkpoint(before)
    model.restore_checkpoint(after)
    assert model.page_count == 4
    model.close()
