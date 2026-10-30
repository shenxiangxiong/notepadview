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


def test_canvas_only_exposes_viewing_controls(app):
    from PySide6.QtWidgets import QPushButton
    d = api()
    model = d.new_diagram('mindmap')
    model['pages'].append(d.new_diagram('flowchart')['pages'][0])
    editor = d.DiagramEditor(model)
    try:
        assert {button.text() for button in editor.findChildren(QPushButton)} == {'−', '＋', '适应'}
        assert '只读' in editor.notice.text()
        assert editor.pages.count() == 2
        editor.pages.setCurrentIndex(1)
        assert editor.page_index == 1
        scale = editor.view.transform().m11()
        editor.zoom(1.25)
        assert editor.view.transform().m11() > scale
        assert editor.model == model
        for operation in ('add_node', 'set_node_text', 'delete_ids', 'export_image', 'auto_layout'):
            assert not hasattr(editor, operation)
    finally:
        editor.close()


@pytest.mark.parametrize('kind,suffix', [('mindmap', '.xmind'), ('flowchart', '.drawio'), ('flowchart', '.nvdiagram')])
def test_window_diagram_is_readonly_and_restores(window, tmp_path, monkeypatch, kind, suffix):
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QFileDialog
    d = api()
    path = tmp_path / ('map' + suffix)
    d.write_diagram(path, d.new_diagram(kind))
    original = path.read_bytes()
    window.new_document('json')
    window.editor.setPlainText('{"keep":1}')
    text_session = window.current_session
    assert window.open_document_path(path)
    session = window.current_session
    assert session.kind == 'diagram'
    canvas = session.diagram
    assert not session.document.isModified()
    for action in (window.save_action, window.save_as_action, window.export_action,
                   window.undo_action, window.redo_action):
        assert not action.isEnabled()
    assert not {'新建思维导图', '新建流程图'} & {action.text() for action in window.findChildren(QAction)}
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: pytest.fail('只读图表不应打开保存对话框'))
    assert not window.save_document()
    assert not window.save_as()
    assert not window.save_to(path)
    assert not window.save_to(tmp_path / 'copy.nvdiagram')
    assert not window.export_to(tmp_path / 'export.html')
    window.undo_current()
    window.redo_current()
    assert path.read_bytes() == original
    assert not (tmp_path / 'copy.nvdiagram').exists()
    window.document_tabs.setCurrentIndex(window._session_tab_index(text_session))
    assert window.editor.source_text() == '{"keep":1}'
    window.document_tabs.setCurrentIndex(window._session_tab_index(session))
    assert session.diagram is canvas
    # 兼容旧版工作区中仍标记为已修改的图表缓存。
    session.document.setModified(True)
    assert window.flush_workspace()
    restored = type(window)(settings=window.settings)
    try:
        assert restored.current_session.kind == 'diagram'
        assert restored.current_session.diagram.model == canvas.model
        assert not restored.current_session.document.isModified()
        assert not restored.save_document()
        assert not restored.save_action.isEnabled()
    finally:
        for s in restored.sessions:
            s.document.setModified(False)
        restored.close()
