import pytest
from PySide6.QtCore import QPointF, QRectF
from test_ui import app  # noqa: F401

from notepadview.diagrams import DiagramEditor, new_diagram


@pytest.mark.parametrize('route', ['orthogonal', 'curve'])
@pytest.mark.parametrize('direction', ['right', 'left', 'down'])
@pytest.mark.parametrize('child_width', [80, 420])
def test_mindmap_connections_use_facing_sides_and_empty_gap(app, route, direction, child_width):
    model = new_diagram('mindmap')
    page = model['pages'][0]
    root = page['nodes'][0]
    root.update(x=0, y=0, width=300, height=50)
    child = {**root, 'id': 'child', 'parent': root['id'], 'width': 80, 'height': 40}
    if direction == 'right':
        child.update(x=328, y=140)
        start, end = QPointF(300, 25), QPointF(328, 160)
    elif direction == 'left':
        child.update(x=-108, y=140)
        start, end = QPointF(0, 25), QPointF(-28, 160)
    else:
        child.update(x=450, y=78)
        start, end = QPointF(150, 50), QPointF(490, 78)
    if direction == 'left':
        child['x'] = -28 - child_width
    elif direction == 'down':
        end = QPointF(child['x'] + child_width / 2, 78)
    child['width'] = child_width
    page['nodes'].append(child)
    page['edges'].append({'id': 'edge', 'source': root['id'], 'target': 'child',
                          'arrow': False, 'text': '', 'style': {
                              'route': route, 'tree_direction': 'down' if direction == 'down' else 'horizontal'}})
    viewer = DiagramEditor(model)
    try:
        path = viewer.edge_items[0].path()
        assert path.pointAtPercent(0) == start
        assert path.pointAtPercent(1) == end
        for i in range(1, 100):
            point = path.pointAtPercent(i / 100)
            if direction == 'right':
                assert 300 <= point.x() <= 328
            elif direction == 'left':
                assert -28 <= point.x() <= 0
            else:
                assert 50 <= point.y() <= 78
            for node in (root, child):
                interior = QRectF(node['x'], node['y'], node['width'], node['height']).adjusted(0.01, 0.01, -0.01, -0.01)
                assert not interior.contains(point)
    finally:
        viewer.close()
