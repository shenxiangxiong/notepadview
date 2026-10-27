from copy import deepcopy
import json
import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox
from test_ui import app, window  # noqa: F401
from notepadview.diagrams import DiagramEditor, new_diagram, read_diagram, write_diagram


def test_drawio_preserves_own_tree_and_edge_bend(app, tmp_path):
    editor = DiagramEditor(new_diagram('mindmap'))
    root = editor.page['nodes'][0]['id']
    editor.add_node(root)
    editor.page['edges'][0]['bend'] = {'x': 320, 'y': 220}
    path = tmp_path / 'tree.drawio'
    write_diagram(path, editor.model)
    result = read_diagram(path)
    assert result['pages'][0]['kind'] == 'mindmap'
    assert result['pages'][0]['nodes'][1]['parent'] == result['pages'][0]['nodes'][0]['id']
    assert result['pages'][0]['edges'][0]['bend'] == {'x': 320, 'y': 220}
    assert not result['warnings']


def test_native_converted_warning_can_be_saved_again(tmp_path):
    model = new_diagram()
    model['warnings'] = ['复杂样式']
    model['protected_paths'] = [str(tmp_path / 'original.drawio')]
    path = tmp_path / 'converted.nvdiagram'
    write_diagram(path, model)
    restored = read_diagram(path)
    write_diagram(path, restored)
    assert restored['protected_paths'] == model['protected_paths']


def test_renamed_complex_original_remains_protected(window, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.Yes)
    path = tmp_path / 'complex.drawio'
    write_diagram(path, new_diagram())
    path.write_text(path.read_text(encoding='utf-8').replace('rounded=1;', 'rounded=1;shadow=1;'), encoding='utf-8')
    original = path.read_bytes()
    assert window.open_document_path(path)
    session = window.current_session
    assert window.rename_document_session(session, 'renamed')
    assert not window.save_to(session.path)
    assert session.path.read_bytes() == original
    target = tmp_path / 'converted.nvdiagram'
    assert window.save_to(target)
    assert window.save_document()


def test_real_mouse_drag_and_delete(app):
    editor = DiagramEditor(new_diagram())
    editor.resize(1100, 700)
    editor.show()
    editor.fit()
    app.processEvents()
    node = editor.page['nodes'][0]
    start = editor.view.mapFromScene(editor.items_by_id[node['id']].sceneBoundingRect().center())
    QTest.mousePress(editor.view.viewport(), Qt.LeftButton, pos=start)
    QTest.mouseMove(editor.view.viewport(), start + QPoint(70, 40), delay=30)
    QTest.mouseRelease(editor.view.viewport(), Qt.LeftButton, pos=start + QPoint(70, 40))
    assert editor.node(node['id'])['x'] != 0
    assert editor.undo_stack.canUndo()
    editor.items_by_id[node['id']].setSelected(True)
    QTest.keyClick(editor.view, Qt.Key_Delete)
    assert not editor.page['nodes']
    editor.undo_stack.undo()
    assert len(editor.page['nodes']) == 1
    editor.close()


def test_xmind_non_tree_never_overwrites(tmp_path):
    model = new_diagram('mindmap')
    path = tmp_path / 'tree.xmind'
    write_diagram(path, model)
    original = path.read_bytes()
    model['pages'][0]['nodes'].append({**deepcopy(model['pages'][0]['nodes'][0]), 'id': 'another'})
    with pytest.raises(ValueError, match='主题树'):
        write_diagram(path, model)
    assert path.read_bytes() == original


def test_unknown_native_structure_rejected(tmp_path):
    model = new_diagram()
    model['pages'][0]['nodes'][0]['future_attachment'] = 'do not lose'
    path = tmp_path / 'future.nvdiagram'
    path.write_text(json.dumps(model), encoding='utf-8')
    with pytest.raises(ValueError):
        read_diagram(path)


def test_drawio_unretained_presentation_requires_protection(tmp_path):
    path = tmp_path / 'styled.drawio'
    write_diagram(path, new_diagram())
    path.write_text(path.read_text(encoding='utf-8').replace('<mxGraphModel>', '<mxGraphModel background="#ff0000" pageWidth="900">'), encoding='utf-8')
    result = read_diagram(path)
    assert result['warnings']
    with pytest.raises(ValueError):
        write_diagram(path, result)


def test_drawio_bend_uses_standard_geometry(app, tmp_path):
    editor = DiagramEditor(new_diagram('mindmap'))
    editor.add_node(editor.page['nodes'][0]['id'])
    editor.page['edges'][0]['bend'] = {'x': 300, 'y': 200}
    path = tmp_path / 'bend.drawio'
    write_diagram(path, editor.model)
    from xml.etree import ElementTree as ET
    point = ET.fromstring(path.read_bytes()).find('.//mxGeometry/Array/mxPoint')
    assert point is not None and float(point.get('x')) == 300


def test_closed_diagram_released_from_stack(window):
    window.new_diagram()
    canvas = window.current_session.diagram
    window.current_session.document.setModified(False)
    assert window.close_document_tab(window.document_tabs.currentIndex())
    assert window.document_area.indexOf(canvas) == -1
