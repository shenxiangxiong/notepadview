import pymupdf as fitz
import pytest
from test_ui import app  # noqa: F401
from notepadview.pdf_model import PdfModel
from notepadview.pdf_editor import PdfEditor


def test_open_restores_previous_move_outside_current_bounds(app):
    with fitz.open() as doc:
        page = doc.new_page(width=300, height=300)
        page.insert_text((40, 65), 'Short', fontsize=16)
        model = PdfModel(doc.tobytes())
        initial = model.snapshot()
        model.close()
    state = {'version': 2, 'initial': initial, 'history': [
        {'label': 'Earlier move', 'operations': [
            {'method': 'replace_text', 'args': [0, 0, 'Short'], 'kwargs': {'offset': [-42, 0]}}
        ]}
    ], 'index': 1, 'clean': 0}
    editor = PdfEditor.from_snapshot(state)
    assert editor.undo_stack.index() == 1
    editor.undo_stack.undo()
    assert editor.model.text_blocks(0)[0]['text'] == 'Short'
    editor.undo_stack.redo()
    reopened = PdfEditor.from_snapshot(editor.snapshot())
    assert reopened.undo_stack.index() == 1
    reopened.close()
    editor.close()


def test_history_restores_final_position_only_once(app, monkeypatch):
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 65), 'Short')
        model = PdfModel(doc.tobytes())
        initial = model.snapshot()
        model.close()
    history = [
        {'label': str(index), 'operations': [
            {'method': 'replace_text', 'args': [0, 0, text], 'kwargs': {}}
        ]}
        for index, text in enumerate(['First', 'Second', 'Third'])
    ]
    calls = []
    restore = PdfModel.restore_checkpoint

    def tracked_restore(model, state):
        if model.checkpoint() != state:
            calls.append(state)
        return restore(model, state)

    monkeypatch.setattr(PdfModel, 'restore_checkpoint', tracked_restore)
    editor = PdfEditor.from_snapshot({
        'version': 2, 'initial': initial, 'history': history, 'index': 2, 'clean': 0,
    })
    assert len(calls) == 1
    assert editor.model.text_blocks(0)[0]['text'] == 'Second'
    assert editor.undo_stack.cleanIndex() == 0
    assert editor.undo_stack.index() == 2
    editor.undo_stack.redo()
    assert editor.model.text_blocks(0)[0]['text'] == 'Third'
    editor.undo_stack.undo()
    assert editor.model.text_blocks(0)[0]['text'] == 'Second'
    editor.close()
    editor.deleteLater()


def test_new_move_outside_page_still_rolls_back():
    with fitz.open() as doc:
        page = doc.new_page(width=300, height=300)
        page.insert_text((40, 65), 'Short', fontsize=16)
        model = PdfModel(doc.tobytes())
    with pytest.raises(ValueError, match='请将文字放在页面内'):
        model.replace_text(0, 0, 'Short', offset=(-42, 0))
    assert model.text_blocks(0)[0]['text'] == 'Short'
    model.close()


def test_open_restores_previously_accepted_text_history(app):
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 65), 'Short', fontsize=16)
        initial = PdfModel(doc.tobytes()).snapshot()
    state = {'version': 2, 'initial': initial, 'history': [
        {'label': 'Earlier edit', 'operations': [
            {'method': 'replace_text', 'args': [0, 0, 'A longer earlier edit'], 'kwargs': {}}
        ]}
    ], 'index': 1, 'clean': 0}
    editor = PdfEditor.from_snapshot(state)
    assert editor.model.text_blocks(0)[0]['text'] == 'A longer earlier edit'
    editor.undo_stack.undo()
    assert editor.model.text_blocks(0)[0]['text'] == 'Short'
    editor.undo_stack.redo()
    assert editor.model.text_blocks(0)[0]['text'] == 'A longer earlier edit'
    restored = PdfEditor.from_snapshot(editor.snapshot())
    assert restored.model.text_blocks(0)[0]['text'] == 'A longer earlier edit'
    restored.model.replace_text(0, 0, 'W' * 100)
    with restored.model.open() as doc:
        assert doc[0].get_text().count('W') == 100
    restored.close()
    restored.deleteLater()
    editor.close()
    editor.deleteLater()
