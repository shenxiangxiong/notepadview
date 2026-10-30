import json
import zipfile
from copy import deepcopy

import pytest
from PySide6.QtCore import QRectF, Qt
from test_ui import app  # noqa: F401

from notepadview.diagram_formats import read_diagram, validate, write_diagram
from notepadview.diagrams import DiagramEditor


def summary_file(tmp_path, structure='org.xmind.ui.logic.right', summary_range='(0,2)'):
    root = {'id': 'root', 'title': 'Root', 'structureClass': structure,
            'children': {'attached': [
                {'id': 'a', 'title': 'Reported issues'},
                {'id': 'b', 'title': '1. Show benefits on home page', 'children': {'attached': [
                    {'id': 'detail', 'title': 'More details'}]}},
                {'id': 'c', 'title': '2. Show discount'},
                {'id': 'd', 'title': 'Outside the summary'}],
                'summary': [{'id': 's', 'title': 'Include in store improvements',
                             'style': {'properties': {'fo:color': '#ffffffff', 'fo:font-weight': 700}}}]},
            'summaries': [{'id': 'group', 'topicId': 's', 'range': summary_range}]}
    path = tmp_path / 'summary.xmind'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('content.json', json.dumps([{'id': 'sheet', 'rootTopic': root}]))
    return path


def rect(node):
    return QRectF(node['x'], node['y'], node['width'], node['height'])


@pytest.mark.parametrize('structure', ['org.xmind.ui.logic.right', 'org.xmind.ui.logic.left',
                                       'org.xmind.ui.org-chart.down'])
def test_summary_uses_range_and_bracket_instead_of_parent_edge(app, tmp_path, structure):
    path = summary_file(tmp_path, structure)
    original = path.read_bytes()
    model = read_diagram(path)
    page = model['pages'][0]
    assert page['summaries'][0]['topics'] == ['a', 'b', 'c']
    assert page['summaries'][0]['topic'] == 's'
    assert not model['warnings']
    assert not any(e['target'] == 's' for e in page['edges'])
    nodes = {n['id']: n for n in page['nodes']}
    selected = rect(nodes['a']).united(rect(nodes['b'])).united(rect(nodes['c'])).united(rect(nodes['detail']))
    summary = rect(nodes['s'])
    if structure.endswith('right'):
        assert summary.left() > selected.right()
        assert summary.center().y() == pytest.approx(selected.center().y())
    elif structure.endswith('left'):
        assert summary.right() < selected.left()
    else:
        assert summary.top() > selected.bottom()
    for other in nodes.values():
        if other['id'] != 's':
            assert not summary.intersects(rect(other))
    viewer = DiagramEditor(model)
    try:
        assert len(viewer.summary_items) == 1
        assert not viewer.summary_items[0].path().isEmpty()
        assert viewer.summary_items[0].acceptedMouseButtons() == Qt.NoButton
        assert viewer.items_by_id['s'].label.defaultTextColor().name() == '#ffffff'
        assert viewer.items_by_id['s'].label.font().bold()
    finally:
        viewer.close()
    saved = tmp_path / 'restored.nvdiagram'
    write_diagram(saved, model)
    assert read_diagram(saved) == model
    assert path.read_bytes() == original


@pytest.mark.parametrize('summary_range', ['(-1,2)', '(3,1)', '(0,99)', 'bad'])
def test_invalid_summary_range_is_reported_without_fabricated_connection(tmp_path, summary_range):
    model = read_diagram(summary_file(tmp_path, summary_range=summary_range))
    assert not model['pages'][0].get('summaries')
    assert not any(e['target'] == 's' for e in model['pages'][0]['edges'])
    assert any('概要' in warning for warning in model['warnings'])


def test_summary_validation_rejects_missing_or_unrelated_topics(tmp_path):
    model = read_diagram(summary_file(tmp_path))
    assert model['pages'][0].get('summaries')
    for topics in (['missing'], ['detail'], [], ['a', 'a']):
        invalid = deepcopy(model)
        invalid['pages'][0]['summaries'][0]['topics'] = topics
        with pytest.raises(ValueError):
            validate(invalid)


def test_partial_range_long_summary_keeps_neighbor_clear(app, tmp_path):
    path = summary_file(tmp_path, summary_range='(1,2)')
    with zipfile.ZipFile(path) as archive:
        sheets = json.loads(archive.read('content.json'))
    summary = sheets[0]['rootTopic']['children']['summary'][0]
    summary['title'] = '\n'.join(['A detailed summary'] * 20)
    summary['children'] = {'attached': [{'id': 'next', 'title': 'Next step'}]}
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('content.json', json.dumps(sheets))
    page = read_diagram(path)['pages'][0]
    nodes = {n['id']: n for n in page['nodes']}
    assert page['summaries'][0]['topics'] == ['b', 'c']
    for target in ('s', 'next'):
        for other in nodes:
            if target != other:
                assert not rect(nodes[target]).intersects(rect(nodes[other]))


def test_xmind_text_style_and_padding_are_kept_without_scaling(app, tmp_path):
    path = tmp_path / 'text.xmind'
    properties = {'fo:color': '#ffffffff', 'fo:font-weight': 700, 'fo:font-style': 'italic',
                  'fo:text-decoration': 'underline', 'fo:text-align': 'right', 'fo:font-size': '18pt'}
    root = {'id': 'root', 'title': 'Root', 'children': {'attached': [
        {'id': 'a', 'title': 'First line\nA long paragraph that must wrap naturally in this box',
         'customWidth': 150, 'style': {'properties': properties}}]}}
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('content.json', json.dumps([{'id': 'sheet', 'rootTopic': root}]))
    model = read_diagram(path)
    node = model['pages'][0]['nodes'][1]
    assert node['style']['text_color'] == '#ffffff'
    assert node['style']['bold']
    assert node['style']['font_size'] == 18
    viewer = DiagramEditor(model)
    try:
        item = viewer.items_by_id['a']
        assert item.label.scale() == 1
        assert item.label.font().italic() and item.label.font().underline()
        assert item.label.document().defaultTextOption().alignment() == Qt.AlignRight
        label_rect = item.label.mapRectToParent(item.label.boundingRect())
        assert label_rect.left() >= 12
        assert label_rect.right() <= node['width'] - 12 + 0.01
        assert label_rect.top() >= 6
        assert label_rect.bottom() <= node['height'] - 6 + 0.01
    finally:
        viewer.close()
