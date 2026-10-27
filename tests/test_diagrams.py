import base64
import json
import zipfile
import zlib
from urllib.parse import quote

import pytest
from PySide6.QtWidgets import QMessageBox
from test_ui import app, window  # noqa: F401


def api():
    from notepadview import diagrams
    return diagrams


def test_native_and_external_edit_roundtrips(tmp_path):
    d = api()
    model = d.new_diagram('mindmap')
    root = model['pages'][0]['nodes'][0]
    root['text'] = '中心中文'
    for suffix in ('.nvdiagram', '.drawio', '.xmind'):
        path = tmp_path / ('map' + suffix)
        d.write_diagram(path, model)
        restored = d.read_diagram(path)
        assert restored['pages'][0]['nodes'][0]['text'] == '中心中文'
        assert not restored['warnings']
        restored['pages'][0]['nodes'][0]['text'] = '已编辑'
        d.write_diagram(path, restored)
        assert d.read_diagram(path)['pages'][0]['nodes'][0]['text'] == '已编辑'


def test_drawio_compressed_pages_and_protection(tmp_path):
    d = api()
    xml = '<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/><mxCell id="a" value="Hello" vertex="1" parent="1" style="rounded=1;whiteSpace=wrap;html=1;"><mxGeometry x="10" y="20" width="120" height="50" as="geometry"/></mxCell></root></mxGraphModel>'
    compressor = zlib.compressobj(wbits=-15)
    compressed = base64.b64encode(compressor.compress(quote(xml).encode()) + compressor.flush()).decode()
    path = tmp_path / 'pages.drawio'
    path.write_text(f'<mxfile><diagram id="p1" name="一">{compressed}</diagram><diagram id="p2" name="二">{xml}</diagram></mxfile>', encoding='utf-8')
    model = d.read_diagram(path)
    assert len(model['pages']) == 2
    assert model['pages'][0]['nodes'][0]['shape'] == 'rounded'
    assert not model['warnings']
    path.write_text(path.read_text(encoding='utf-8').replace('rounded=1;', 'rounded=1;shadow=1;'), encoding='utf-8')
    original = path.read_bytes()
    model = d.read_diagram(path)
    assert model['warnings'] and model['protected_paths']
    with pytest.raises(ValueError, match='原文件'):
        d.write_diagram(path, model)
    assert path.read_bytes() == original
    d.write_diagram(tmp_path / 'converted.drawio', model)
    d.write_diagram(tmp_path / 'converted.nvdiagram', model)
    with pytest.raises(ValueError):
        d.write_diagram(path, d.read_diagram(tmp_path / 'converted.nvdiagram'))


def test_unsafe_xml_and_zip_rejected(tmp_path):
    d = api()
    path = tmp_path / 'unsafe.drawio'
    path.write_text('<!DOCTYPE mxfile [<!ENTITY a "boom">]><mxfile>&a;</mxfile>')
    with pytest.raises(ValueError):
        d.read_diagram(path)
    path = tmp_path / 'unsafe.xmind'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('content.json', json.dumps([{'id': 's', 'rootTopic': {'id': 'r', 'title': 'Root', 'children': {'attached': [{'id': 'c', 'title': 'Child'}]}, 'notes': {'plain': {'content': 'important'}}}}]))
        archive.writestr('attachments/file.txt', 'attachment')
    model = d.read_diagram(path)
    assert len(model['pages'][0]['nodes']) == 2
    assert model['warnings']
    with pytest.raises(ValueError):
        d.write_diagram(path, model)


def test_canvas_undo_dirty_layout_delete_export(app, tmp_path):
    d = api()
    editor = d.DiagramEditor(d.new_diagram('mindmap'))
    root = editor.model['pages'][0]['nodes'][0]['id']
    child = editor.add_node(parent_id=root)
    assert len(editor.model['pages'][0]['edges']) == 1
    assert not editor.undo_stack.isClean()
    editor.set_node_text(child, '子主题')
    editor.move_node(child, 400, 200)
    editor.undo_stack.undo()
    assert editor.node(child)['x'] != 400
    editor.undo_stack.redo()
    assert editor.node(child)['x'] == 400
    editor.auto_layout()
    editor.delete_ids({root})
    assert not editor.model['pages'][0]['edges']
    editor.undo_stack.undo()
    assert len(editor.model['pages'][0]['nodes']) == 2
    for suffix in ('.svg', '.png'):
        path = tmp_path / ('export' + suffix)
        editor.export_image(path)
        assert path.stat().st_size > 100
    while editor.undo_stack.canUndo():
        editor.undo_stack.undo()
    assert editor.undo_stack.isClean()
    editor.close()


def test_window_diagram_tabs_save_and_restore(window, tmp_path, monkeypatch):
    d = api()
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, 'information', lambda *a: QMessageBox.Ok)
    path = tmp_path / 'map.xmind'
    d.write_diagram(path, d.new_diagram('mindmap'))
    window.new_document('json')
    window.editor.setPlainText('{"keep":1}')
    text_session = window.current_session
    assert window.open_document_path(path)
    session = window.current_session
    assert session.kind == 'diagram'
    canvas = session.diagram
    child = canvas.add_node(parent_id=canvas.model['pages'][0]['nodes'][0]['id'])
    canvas.set_node_text(child, '新增')
    assert session.document.isModified()
    assert window.save_action.isEnabled()
    assert window.save_document()
    assert not session.document.isModified()
    canvas.undo_stack.undo()
    assert session.document.isModified()
    canvas.undo_stack.redo()
    assert not session.document.isModified()
    window.document_tabs.setCurrentIndex(window._session_tab_index(text_session))
    assert window.editor.source_text() == '{"keep":1}'
    window.document_tabs.setCurrentIndex(window._session_tab_index(session))
    assert session.diagram is canvas
    assert window.rename_document_session(session, 'renamed')
    canvas.set_node_text(child, '恢复未保存内容')
    assert window.flush_workspace()
    restored = type(window)(settings=window.settings)
    try:
        assert restored.current_session.kind == 'diagram'
        assert restored.current_session.diagram.node(child)['text'] == '恢复未保存内容'
        assert restored.current_session.document.isModified()
        assert restored.save_document()
        assert d.read_diagram(restored.path)['pages'][0]['nodes'][1]['text'] == '恢复未保存内容'
    finally:
        for s in restored.sessions:
            s.document.setModified(False)
        restored.close()
