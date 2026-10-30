import json
import zipfile
from copy import deepcopy

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from test_ui import app, window  # noqa: F401
from notepadview.diagrams import DiagramEditor, new_diagram, read_diagram


@pytest.mark.parametrize('kind', ['mindmap', 'flowchart'])
def test_pan_at_fit_and_zoom_without_mutation(app, kind):
    viewer = DiagramEditor(new_diagram(kind))
    viewer.resize(1000, 650)
    viewer.show()
    app.processEvents()
    original = deepcopy(viewer.model)
    try:
        viewer.fit()
        for factor in (1, 0.25, 8):
            viewer.zoom(factor)
            start = viewer.view.viewport().rect().center()
            end = start + QPoint(90, 50)
            before = viewer.view.mapToScene(start)
            QTest.mousePress(viewer.view.viewport(), Qt.LeftButton, pos=start)
            QTest.mouseMove(viewer.view.viewport(), end, delay=20)
            QTest.mouseRelease(viewer.view.viewport(), Qt.LeftButton, pos=end)
            after = viewer.view.mapToScene(start)
            assert after.x() < before.x() and after.y() < before.y()
        assert viewer.model == original
    finally:
        viewer.close()


def test_xmind_theme_layout_and_embedded_image(app, window, tmp_path):
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QImage
    image = QImage(20, 10, QImage.Format_RGB32)
    image.fill(Qt.red)
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, 'PNG')
    root = {'id': 'root', 'title': 'Root', 'structureClass': 'org.xmind.ui.logic.right',
            'children': {'attached': [
                {'id': 'a', 'title': 'First\nline', 'href': 'https://example.com/picture', 'style': {'properties': {'fo:color': '#ff0000ff'}},
                 'image': {'src': 'xap:resources/picture.png', 'width': 80, 'height': 40},
                 'children': {'attached': [{'id': 'c', 'title': 'Child'}]}},
                {'id': 'b', 'title': 'Second'}]}}
    theme = {
        'centralTopic': {'properties': {'svg:fill': '#070d59ff', 'fo:color': '#ffffffff', 'fo:font-size': '24pt'}},
        'mainTopic': {'properties': {'fo:font-size': '18pt'}},
        'subTopic': {'properties': {'fo:font-size': '14pt'}},
        'map': {'properties': {'multi-line-colors': '#FFC947 #E46D57'}}}
    path = tmp_path / 'original.xmind'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('content.json', json.dumps([{'id': 'sheet', 'title': 'Sheet', 'rootTopic': root, 'theme': theme}]))
        archive.writestr('resources/picture.png', bytes(buffer.data()))
    model = read_diagram(path)
    page = model['pages'][0]
    nodes = {n['id']: n for n in page['nodes']}
    assert nodes['root']['style']['fill'] == '#4bc48a'
    assert nodes['a']['style']['text_color'] == '#ff0000'
    assert nodes['root']['style']['font_size'] == 16
    assert nodes['a']['image']['data']
    assert nodes['a']['x'] > nodes['root']['x'] + nodes['root']['width']
    assert nodes['c']['x'] > nodes['a']['x'] + nodes['a']['width']
    assert nodes['b']['y'] >= nodes['a']['y'] + nodes['a']['height']
    assert all(not e['arrow'] for e in page['edges'])
    viewer = DiagramEditor(model)
    try:
        item = viewer.items_by_id['root']
        assert item.brush().color().name() == '#4bc48a'
        assert item.label.font().pointSizeF() == 16
        assert viewer.items_by_id['a'].label.toPlainText() == 'First\nline'
        assert any(child.__class__.__name__ == 'QGraphicsPixmapItem' for child in viewer.items_by_id['a'].childItems())
    finally:
        viewer.close()
    from PySide6.QtWidgets import QMessageBox
    from unittest.mock import patch
    with patch.object(QMessageBox, 'warning', return_value=QMessageBox.Ok):
        assert window.open_document_path(path)
    assert window.flush_workspace()
    restored = type(window)(settings=window.settings)
    try:
        actual = restored.current_session.diagram.model
        expected = window.current_session.diagram.model
        assert actual['pages'][0]['nodes'] == expected['pages'][0]['nodes']
        assert actual['pages'][0].get('background') == expected['pages'][0].get('background')
        assert [{k: v for k, v in e.items() if k != 'id'} for e in actual['pages'][0]['edges']] == [
            {k: v for k, v in e.items() if k != 'id'} for e in expected['pages'][0]['edges']]
        assert not restored.current_session.document.isModified()
    finally:
        restored.close()


def test_xmind_compact_screenshot_style_and_clickable_link(app, tmp_path):
    from unittest.mock import patch
    from PySide6.QtCore import QUrl
    path = tmp_path / 'linked.xmind'
    root = {'id': 'root', 'title': 'Root', 'structureClass': 'org.xmind.ui.logic.right',
            'children': {'attached': [{'id': 'a', 'title': 'First', 'href': 'https://example.com/report'},
                                      {'id': 'b', 'title': 'Second', 'children': {'attached': [
                                          {'id': 'c', 'title': 'Child', 'href': 'javascript:alert(1)'}]}}]}}
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('content.json', json.dumps([{'id': 'sheet', 'rootTopic': root}]))
    model = read_diagram(path)
    nodes = {n['id']: n for n in model['pages'][0]['nodes']}
    assert nodes['root']['style']['fill'] == '#4bc48a'
    assert nodes['a']['style']['fill'] == '#ffcc45'
    assert nodes['c']['style']['fill'] == nodes['b']['style']['fill'] == '#ed6a55'
    assert nodes['b']['height'] < 40
    assert nodes['a']['height'] < 70
    assert nodes['a']['link'] == 'https://example.com/report'
    assert 'link' not in nodes['c']
    viewer = DiagramEditor(model)
    viewer.resize(1000, 650)
    viewer.show()
    app.processEvents()
    try:
        link = viewer.items_by_id['a'].link_item
        viewer.view.center_at(link.sceneBoundingRect().center())
        point = viewer.view.mapFromScene(link.sceneBoundingRect().center())
        with patch('notepadview.diagrams.QDesktopServices.openUrl', return_value=True) as opened:
            QTest.mouseClick(viewer.view.viewport(), Qt.LeftButton, pos=point)
            opened.assert_called_once_with(QUrl('https://example.com/report'))
        before = viewer.view.mapToScene(point)
        with patch('notepadview.diagrams.QDesktopServices.openUrl') as opened:
            QTest.mousePress(viewer.view.viewport(), Qt.LeftButton, pos=point)
            QTest.mouseMove(viewer.view.viewport(), point + QPoint(60, 40), delay=20)
            QTest.mouseRelease(viewer.view.viewport(), Qt.LeftButton, pos=point + QPoint(60, 40))
            opened.assert_not_called()
        assert viewer.view.mapToScene(point) != before
    finally:
        viewer.close()


@pytest.mark.parametrize('source_state', ['available', 'missing', 'corrupt'])
def test_workspace_reloads_diagram_source_instead_of_stale_snapshot(window, tmp_path, source_state):
    from unittest.mock import patch
    from PySide6.QtWidgets import QMessageBox
    path = tmp_path / 'cached.xmind'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('content.json', json.dumps([{'id': 'sheet', 'rootTopic': {'id': 'root', 'title': 'Original'}}]))
    with patch.object(QMessageBox, 'warning', return_value=QMessageBox.Ok):
        assert window.open_document_path(path)
    session = window.current_session
    session.diagram.model['pages'][0]['nodes'][0]['text'] = 'Stale snapshot'
    session.diagram.model['pages'][0]['nodes'][0].pop('style', None)
    assert window.flush_workspace()
    if source_state == 'missing':
        path.unlink()
    elif source_state == 'corrupt':
        path.write_bytes(b'not a valid archive')
    restored = type(window)(settings=window.settings)
    try:
        node = restored.current_session.diagram.model['pages'][0]['nodes'][0]
        if source_state == 'available':
            assert node['text'] == 'Original'
            assert node['style']['fill'] == '#4bc48a'
        else:
            assert node['text'] == 'Stale snapshot'
        assert not restored.current_session.document.isModified()
    finally:
        restored.close()


def test_large_diagram_opens_readably_and_keeps_pan_on_reshow(app):
    model = new_diagram('mindmap')
    root = model['pages'][0]['nodes'][0]
    model['pages'][0]['nodes'].extend({**root, 'id': str(i), 'x': 400, 'y': i * 120} for i in range(100))
    viewer = DiagramEditor(model)
    viewer.resize(1000, 650)
    viewer.show()
    app.processEvents()
    try:
        assert viewer.view.transform().m11() >= 0.5
        viewer.zoom(1.25)
        scale = viewer.view.transform().m11()
        center = viewer.view.mapToScene(viewer.view.viewport().rect().center())
        viewer.hide()
        viewer.show()
        app.processEvents()
        assert viewer.view.transform().m11() == scale
        assert viewer.view.mapToScene(viewer.view.viewport().rect().center()) == center
        viewer.fit()
        assert viewer.view.transform().m11() < scale
    finally:
        viewer.close()


@pytest.mark.parametrize('structure', ['logic.left', 'org-chart.down', 'map'])
def test_xmind_respects_common_structure_directions(tmp_path, structure):
    root = {'id': 'root', 'title': 'Root', 'structureClass': 'org.xmind.ui.' + structure,
            'children': {'attached': [{'id': 'a', 'title': 'First'}, {'id': 'b', 'title': 'Second'}]}}
    path = tmp_path / 'layout.xmind'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('content.json', json.dumps([{'id': 'sheet', 'rootTopic': root}]))
    nodes = read_diagram(path)['pages'][0]['nodes']
    root, a, b = nodes
    if structure == 'logic.left':
        assert max(a['x'] + a['width'], b['x'] + b['width']) < root['x']
    elif structure == 'org-chart.down':
        assert min(a['y'], b['y']) > root['y'] + root['height']
    else:
        assert a['x'] > root['x'] + root['width']
        assert b['x'] + b['width'] < root['x']


@pytest.mark.parametrize('field,value', [('theme', None), ('theme', []), ('style', 1)])
def test_malformed_xmind_appearance_reports_read_error(tmp_path, field, value):
    path = tmp_path / 'broken.xmind'
    sheet = {'id': 'sheet', 'rootTopic': {'id': 'root', 'title': 'Root'}, field: value}
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('content.json', json.dumps([sheet]))
    with pytest.raises(ValueError):
        read_diagram(path)


def test_underline_topic_has_valid_connectors(app):
    model = new_diagram('mindmap')
    root = model['pages'][0]['nodes'][0]
    root['shape'] = 'underline'
    model['pages'][0]['nodes'].append({**root, 'id': 'child', 'x': 300, 'parent': root['id']})
    model['pages'][0]['edges'].append({'id': 'edge', 'source': root['id'], 'target': 'child', 'arrow': False})
    viewer = DiagramEditor(model)
    try:
        assert not viewer.edge_items[0].path().isEmpty()
    finally:
        viewer.close()


def test_external_writer_rejects_lossy_appearance(tmp_path):
    from notepadview.diagrams import write_diagram
    model = new_diagram()
    model['pages'][0]['nodes'][0]['style'] = {'fill': '#ff0000'}
    for suffix in ('.drawio', '.xmind'):
        path = tmp_path / ('styled' + suffix)
        with pytest.raises(ValueError, match='样式'):
            write_diagram(path, model)
        assert not path.exists()
    path = tmp_path / 'styled.nvdiagram'
    write_diagram(path, model)
    assert read_diagram(path) == model


def test_drawio_original_style_and_route(app, tmp_path):
    path = tmp_path / 'styled.drawio'
    path.write_text('''<mxGraphModel background="#f0f0f0"><root><mxCell id="0"/><mxCell id="1" parent="0"/>
    <mxCell id="a" value="First" link="https://example.com/flow" vertex="1" parent="1" style="ellipse;fillColor=#ffcc00;strokeColor=#123456;fontColor=#880000;fontSize=20;fontStyle=1;"><mxGeometry x="20" y="30" width="160" height="80" as="geometry"/></mxCell>
    <mxCell id="b" value="Second" vertex="1" parent="1"><mxGeometry x="300" y="200" width="120" height="60" as="geometry"/></mxCell>
    <mxCell id="e" source="a" target="b" edge="1" parent="1" style="edgeStyle=orthogonalEdgeStyle;strokeColor=#008800;strokeWidth=3;dashed=1;endArrow=none;"><mxGeometry relative="1" as="geometry"><Array as="points"><mxPoint x="240" y="70"/><mxPoint x="240" y="230"/></Array></mxGeometry></mxCell>
    </root></mxGraphModel>''', encoding='utf-8')
    model = read_diagram(path)
    page = model['pages'][0]
    assert page['nodes'][0]['link'] == 'https://example.com/flow'
    assert page['nodes'][0]['shape'] == 'ellipse'
    assert page['nodes'][0]['style']['fill'] == '#ffcc00'
    assert page['edges'][0]['points'] == [{'x': 240, 'y': 70}, {'x': 240, 'y': 230}]
    viewer = DiagramEditor(model)
    try:
        assert viewer.view.backgroundBrush().color().name() == '#f0f0f0'
        assert viewer.edge_items[0].pen().color().name() == '#008800'
        assert viewer.edge_items[0].pen().style() == Qt.DashLine
        assert viewer.items_by_id['a'].label.font().bold()
    finally:
        viewer.close()
