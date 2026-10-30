"""本地画布格式与保守的 draw.io / XMind 读写。"""
import base64
from copy import deepcopy
from html import unescape
import io
import json
import math
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import uuid4
import xml.etree.ElementTree as ET
import zipfile
import zlib

from PySide6.QtCore import QIODevice, QSaveFile
from notepadview.diagram_appearance import (STYLE_KEYS, color, drawio_style, layout_tree,
                                           number, topic_size, xmind_style)

MAX_BYTES = 16 * 1024 * 1024
MAX_NODES = 5000
SUFFIXES = ('.nvdiagram', '.drawio', '.xmind')


def identifier():
    return uuid4().hex


def new_diagram(kind='flowchart'):
    node = {'id': identifier(), 'text': '中心主题' if kind == 'mindmap' else '开始',
            'x': 0.0, 'y': 0.0, 'width': 150.0, 'height': 64.0, 'shape': 'rounded', 'parent': None}
    return {'version': 1, 'type': 'notepadview-diagram', 'pages': [
        {'id': identifier(), 'name': '第 1 页', 'kind': kind, 'nodes': [node], 'edges': []}],
        'warnings': [], 'protected_paths': []}


def web_link(value):
    if not isinstance(value, str) or len(value) > 8192 or any(ord(c) < 32 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() in ('http', 'https') and parsed.hostname and not parsed.username:
            return value
    except ValueError:
        pass
    return None


def validate(model):
    if not isinstance(model, dict) or model.get('version') != 1 or model.get('type') != 'notepadview-diagram':
        raise ValueError('无法识别画布版本。')
    pages = model.get('pages')
    if not isinstance(pages, list) or not 1 <= len(pages) <= 100:
        raise ValueError('画布页数必须在 1 至 100 之间。')
    if not isinstance(model.get('warnings', []), list) or not all(isinstance(x, str) for x in model.get('warnings', [])):
        raise ValueError('无效的格式警告。')
    if not isinstance(model.get('protected_paths', []), list) or not all(isinstance(x, str) for x in model.get('protected_paths', [])):
        raise ValueError('无效的原文件保护记录。')
    page_ids = set()
    count = 0
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get('id'), str) or page['id'] in page_ids:
            raise ValueError('无效或重复的页面 ID。')
        if set(page) - {'id', 'name', 'kind', 'nodes', 'edges', 'background', 'summaries'}:
            raise ValueError('页面包含未支持的属性。')
        page_ids.add(page['id'])
        if 'background' in page and (not isinstance(page['background'], str) or len(page['background']) > 200):
            raise ValueError('无效的画布背景色。')
        if page.get('kind') not in ('mindmap', 'flowchart') or not isinstance(page.get('name'), str):
            raise ValueError('无效的页面类型或名称。')
        if not isinstance(page.get('nodes'), list) or not isinstance(page.get('edges'), list):
            raise ValueError('无效的节点或连线。')
        ids = set()
        for node in page['nodes']:
            if not isinstance(node, dict) or set(node) - {'id', 'text', 'x', 'y', 'width', 'height', 'shape', 'parent', 'style', 'image', 'link'}:
                raise ValueError('节点结构无效或包含未支持的属性。')
            count += 1
            if count > MAX_NODES:
                raise ValueError('节点超过 5000 个，无法打开。')
            if not isinstance(node.get('id'), str) or not node['id'] or node['id'] in ids:
                raise ValueError('节点 ID 无效或重复。')
            ids.add(node['id'])
            if not isinstance(node.get('text'), str) or len(node['text']) > 100000:
                raise ValueError('节点文本无效或过长。')
            if node.get('shape') not in ('rectangle', 'rounded', 'diamond', 'ellipse', 'underline'):
                raise ValueError('不支持的节点形状。')
            _validate_style(node.get('style', {}))
            if 'link' in node and web_link(node['link']) is None:
                raise ValueError('无效的图表链接。')
            image = node.get('image')
            if image is not None:
                if (not isinstance(image, dict) or set(image) != {'data', 'width', 'height'}
                        or not isinstance(image['data'], str) or len(image['data']) > MAX_BYTES
                        or any(type(image[k]) not in (int, float) or not math.isfinite(image[k])
                               or not 1 <= image[k] <= 2000 for k in ('width', 'height'))):
                    raise ValueError('无效的图表图片。')
            for key in ('x', 'y', 'width', 'height'):
                v = node.get(key)
                if type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1000000:
                    raise ValueError('节点坐标或尺寸无效。')
            if not 10 <= node['width'] <= 10000 or not 10 <= node['height'] <= 10000:
                raise ValueError('节点尺寸超出范围。')
        parents = {n['id']: n.get('parent') for n in page['nodes']}
        for node in page['nodes']:
            seen = set()
            current = node['id']
            while current is not None:
                if current in seen or current not in parents or len(seen) > 200:
                    raise ValueError('主题树存在循环、缺失父主题或超过 200 层。')
                seen.add(current)
                current = parents[current]
        summaries = page.get('summaries', [])
        if not isinstance(summaries, list) or len(summaries) > MAX_NODES:
            raise ValueError('无效的概要列表。')
        summary_ids, summary_topics = set(), set()
        for summary in summaries:
            if (not isinstance(summary, dict) or set(summary) != {'id', 'topic', 'topics', 'style'}
                    or not isinstance(summary['id'], str) or summary['id'] in summary_ids
                    or not isinstance(summary['topic'], str) or summary['topic'] not in ids
                    or summary['topic'] in summary_topics):
                raise ValueError('无效的概要结构。')
            topics = summary['topics']
            if (not isinstance(topics, list) or not topics or len(topics) > MAX_NODES
                    or any(not isinstance(t, str) or t not in ids or t == summary['topic'] for t in topics)
                    or len(set(topics)) != len(topics)
                    or any(parents[t] != parents[summary['topic']] for t in topics)):
                raise ValueError('概要范围引用无效的主题。')
            _validate_style(summary['style'])
            summary_ids.add(summary['id'])
            summary_topics.add(summary['topic'])
        if any(t in summary_topics for s in summaries for t in s['topics']):
            raise ValueError('概要范围不能引用其他概要主题。')
        edge_ids = set(ids)
        if len(page['edges']) > MAX_NODES * 2:
            raise ValueError('连线数量过多。')
        for edge in page['edges']:
            if not isinstance(edge, dict) or set(edge) - {'id', 'source', 'target', 'text', 'arrow', 'bend', 'points', 'style'}:
                raise ValueError('连线结构无效或包含未支持的属性。')
            _validate_style(edge.get('style', {}))
            points = edge.get('points', [])
            if (not isinstance(points, list) or len(points) > 1000
                    or any(not isinstance(p, dict) or set(p) != {'x', 'y'}
                           or any(type(v) not in (int, float) or not math.isfinite(v)
                                  or abs(v) > 1000000 for v in p.values()) for p in points)):
                raise ValueError('无效的连线路径。')
            bend = edge.get('bend')
            if bend is not None and (not isinstance(bend, dict) or set(bend) != {'x', 'y'} or any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1000000 for v in bend.values())):
                raise ValueError('连线折点坐标无效。')
            if not isinstance(edge.get('id'), str) or edge['id'] in edge_ids:
                raise ValueError('连线 ID 无效或重复。')
            edge_ids.add(edge['id'])
            if edge.get('source') not in ids or edge.get('target') not in ids or not isinstance(edge.get('text', ''), str):
                raise ValueError('连线引用了不存在的节点。')
    return model


def _validate_style(style):
    if not isinstance(style, dict) or set(style) - STYLE_KEYS:
        raise ValueError('无效的图表样式。')
    for key, value in style.items():
        if key in ('font_size', 'stroke_width'):
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 200:
                raise ValueError('无效的图表样式尺寸。')
        elif key in ('bold', 'italic', 'underline', 'dashed'):
            if type(value) is not bool:
                raise ValueError('无效的图表样式标记。')
        elif not isinstance(value, str) or len(value) > 200:
            raise ValueError('无效的图表样式文本。')


def _xml(data):
    # 拒绝声明与实体，包括 UTF-16/32 编码中的空字节形式。
    if re.search(br'<!\s*(DOCTYPE|ENTITY)', data.replace(b'\x00', b''), re.I):
        raise ValueError('不接受包含 DTD 或实体声明的 XML。')
    if len(data) > MAX_BYTES:
        raise ValueError('XML 超过 16 MiB 限制。')
    try:
        return ET.fromstring(data)
    except ET.ParseError as error:
        raise ValueError('XML 格式损坏：' + str(error)) from error


def _warn(model, text):
    if text not in model['warnings']:
        model['warnings'].append(text)


def _unknown(model, element, attrs, children, label):
    if set(element.attrib) - set(attrs) or any(child.tag not in children for child in element):
        _warn(model, label + '包含未支持的属性或结构')


def _drawio(data):
    root = _xml(data)
    model = new_diagram()
    model['pages'] = []
    if root.tag == 'mxGraphModel':
        diagrams = [root]
    elif root.tag == 'mxfile':
        _unknown(model, root, ('host', 'modified', 'agent', 'etag', 'version', 'type', 'compressed', 'pages'), ('diagram',), '文件')
        diagrams = list(root)
    else:
        raise ValueError('不是 draw.io 图形文件。')
    for diagram in diagrams:
        page = {'id': diagram.get('id') or identifier(), 'name': diagram.get('name', '画布'),
                'kind': diagram.get('nvKind', 'flowchart'), 'nodes': [], 'edges': []}
        if diagram.tag == 'mxGraphModel':
            graph = diagram
        else:
            _unknown(model, diagram, ('id', 'name', 'nvKind'), ('mxGraphModel',), '页面')
            graph = diagram.find('mxGraphModel')
            if graph is None:
                try:
                    payload = base64.b64decode(''.join((diagram.text or '').split()), validate=True)
                    decoder = zlib.decompressobj(-15)
                    decoded = decoder.decompress(payload, MAX_BYTES + 1)
                    if len(decoded) > MAX_BYTES or decoder.unconsumed_tail or not decoder.eof:
                        raise ValueError('压缩页面过大或损坏。')
                    graph = _xml(unquote(decoded.decode('utf-8'), errors='strict').encode('utf-8'))
                except (zlib.error, UnicodeError, ValueError) as error:
                    raise ValueError('无法解压 draw.io 页面：' + str(error)) from error
        if graph.attrib:
            _warn(model, '部分画布设置可能无法完整显示')
        if (background := color(graph.get('background'))) is not None:
            page['background'] = background
        _unknown(model, graph, ('dx', 'dy', 'grid', 'gridSize', 'guides', 'tooltips', 'connect', 'arrows', 'fold', 'page', 'pageScale', 'pageWidth', 'pageHeight', 'math', 'shadow', 'background'), ('root',), '画布')
        cells = graph.find('root')
        if cells is None:
            raise ValueError('draw.io 页面缺少 root。')
        if cells.attrib:
            _warn(model, 'root 包含未支持属性')
        for cell in cells:
            if cell.tag != 'mxCell':
                _warn(model, '包含分组、对象或特殊结构')
                continue
            _unknown(model, cell, ('id', 'value', 'link', 'style', 'vertex', 'edge', 'parent', 'source', 'target', 'nvParent', 'nvBendX', 'nvBendY'), ('mxGeometry',), '图元')
            if cell.get('id') in ('0', '1') and not cell.get('vertex') and not cell.get('edge'):
                if cell.get('value') or cell.get('style'):
                    _warn(model, '图层属性未支持')
                continue
            style = {}
            for token in cell.get('style', '').split(';'):
                if token:
                    key, _, value = token.partition('=')
                    style[key] = value
            allowed = {'rounded': ('0', '1'), 'whiteSpace': ('wrap',), 'html': ('0', '1'),
                       'rhombus': ('',), 'ellipse': ('',), 'shape': ('rhombus', 'rectangle', 'ellipse'),
                       'edgeStyle': ('none', 'orthogonalEdgeStyle', 'elbowEdgeStyle'), 'endArrow': ('classic', 'none'),
                       'endFill': ('1',), 'orthogonalLoop': ('1',), 'jettySize': ('auto',)}
            appearance_keys = {'fillColor', 'strokeColor', 'fontColor', 'fontSize', 'fontFamily',
                               'fontStyle', 'strokeWidth', 'align', 'dashed', 'curved'}
            if any(key not in appearance_keys and (key not in allowed or value not in allowed[key])
                   for key, value in style.items()):
                _warn(model, '部分复杂样式、锚点或特殊图形可能无法完整显示')
            appearance = drawio_style(style)
            text = cell.get('value', '')
            if style.get('html') == '1':
                text = re.sub(r'<br\s*/?>|</(?:div|p)>', '\n', text, flags=re.I)
                if re.search(r'<[^>]+>', text):
                    _warn(model, '富文本格式将转换为纯文本')
                text = unescape(re.sub(r'<[^>]*>', '', text)).rstrip('\n')
            geo = cell.find('mxGeometry')
            if cell.get('vertex') == '1':
                if cell.get('parent', '1') != '1':
                    _warn(model, '嵌套图元或额外图层未支持')
                if geo is None:
                    raise ValueError('节点缺少几何尺寸。')
                _unknown(model, geo, ('x', 'y', 'width', 'height', 'as'), (), '节点几何')
                node = {'id': cell.get('id'), 'text': text, 'parent': cell.get('nvParent'),
                        'shape': 'diamond' if 'rhombus' in style or style.get('shape') == 'rhombus' else
                                 'ellipse' if 'ellipse' in style or style.get('shape') == 'ellipse' else
                                 'rounded' if style.get('rounded') == '1' else 'rectangle',
                        **({'style': appearance} if appearance else {})}
                for key, default in (('x', 0), ('y', 0), ('width', 150), ('height', 64)):
                    node[key] = float(geo.get(key, default))
                if (link := web_link(cell.get('link'))) is not None:
                    node['link'] = link
                elif cell.get('link'):
                    _warn(model, '非网页链接不自动打开')
                page['nodes'].append(node)
            elif cell.get('edge') == '1':
                route_points = []
                if geo is not None:
                    _unknown(model, geo, ('relative', 'as'), ('Array',), '连线几何')
                    arrays = list(geo)
                    points = geo.findall('./Array/mxPoint')
                    if arrays:
                        if (len(arrays) == 1 and arrays[0].attrib == {'as': 'points'}
                                and len(points) == len(list(arrays[0]))
                                and all(set(p.attrib) <= {'x', 'y'} and not list(p) for p in points)):
                            route_points = [{'x': float(p.get('x', 0)), 'y': float(p.get('y', 0))} for p in points]
                            if len(points) == 1:
                                cell.set('nvBendX', points[0].get('x', '0'))
                                cell.set('nvBendY', points[0].get('y', '0'))
                        else:
                            _warn(model, '部分特殊连线控制点未支持')
                if cell.get('parent', '1') != '1':
                    _warn(model, '嵌套连线未支持')
                page['edges'].append({'id': cell.get('id'), 'source': cell.get('source'),
                                      'target': cell.get('target'), 'text': text,
                                      'arrow': style.get('endArrow') != 'none',
                                      **({'style': appearance} if appearance else {}),
                                      **({'points': route_points} if len(route_points) > 1 else {}),
                                      **({'bend': {'x': float(cell.get('nvBendX')), 'y': float(cell.get('nvBendY'))}} if cell.get('nvBendX') is not None else {})})
            else:
                _warn(model, '额外图层或未识别图元未支持')
        ids = {n['id'] for n in page['nodes']}
        valid_edges = [e for e in page['edges'] if e['source'] in ids and e['target'] in ids]
        if len(valid_edges) != len(page['edges']):
            _warn(model, '游离连线或特殊连接端点未支持')
        page['edges'] = valid_edges
        model['pages'].append(page)
    return model


def _xmind(data):
    model = new_diagram('mindmap')
    model['pages'] = []
    images = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > 2000 or sum(i.file_size for i in infos) > MAX_BYTES * 4:
                raise ValueError('XMind 解压后内容超过限制。')
            names = [i.filename for i in infos]
            if len(names) != len(set(names)):
                raise ValueError('XMind 存在重复 ZIP 条目。')
            if 'content.json' not in names:
                raise ValueError('目前仅支持含 content.json 的新版 XMind；旧版 content.xml 尚不支持。')
            if archive.getinfo('content.json').file_size > MAX_BYTES:
                raise ValueError('XMind content.json 超过 16 MiB。')
            sheets = json.loads(archive.read('content.json'))
            for info in infos:
                if info.filename.startswith('resources/') and info.filename.lower().endswith(('.png', '.jpg', '.jpeg')) and info.file_size <= 4 * 1024 * 1024:
                    images['xap:' + info.filename] = base64.b64encode(archive.read(info)).decode('ascii')
            if any(name not in ('content.json', 'metadata.json', 'manifest.json', 'Thumbnails/thumbnail.png') and not name.endswith('/') for name in names):
                _warn(model, 'XMind 包含附件或其他附加文件')
            if 'metadata.json' in names:
                metadata = json.loads(archive.read('metadata.json'))
                if set(metadata) - {'creator', 'activeSheetId'}:
                    _warn(model, 'XMind 包含额外元数据')
    except (zipfile.BadZipFile, RuntimeError, KeyError) as error:
        raise ValueError('无法读取 XMind ZIP：' + str(error)) from error
    if not isinstance(sheets, list):
        raise ValueError('无效的 XMind 主题内容。')
    def topic(node, page, theme, parent=None, depth=0, branch_color=None, is_summary=False):
        if depth > 200 or len(page['nodes']) >= MAX_NODES or not isinstance(node, dict):
            raise ValueError('主题层数或数量超过限制。')
        if set(node) - {'id', 'title', 'children', 'class', 'nvPosition', 'structureClass',
                        'style', 'image', 'href', 'customWidth', 'summaries'}:
            _warn(model, '主题包含备注、标签、标记或其他未支持信息')
        node_properties = node.get('style', {}).get('properties', {})
        if set(node_properties) - {'svg:fill', 'border-line-color', 'fo:color', 'fo:font-family',
                'fo:font-size', 'border-line-width', 'fo:font-weight', 'fo:font-style',
                'fo:text-decoration', 'fo:text-align', 'shape-class', 'line-color', 'line-class'}:
            _warn(model, '主题包含未支持的复杂样式')
        structure = node.get('structureClass', '')
        if structure and structure not in ('org.xmind.ui.logic.right', 'org.xmind.ui.logic.left',
                'org.xmind.ui.map.unbalanced', 'org.xmind.ui.map.clockwise', 'org.xmind.ui.map',
                'org.xmind.ui.org-chart.down', 'org.xmind.ui.tree.down', 'org.xmind.ui.tree.right', 'org.xmind.ui.tree.left'):
            _warn(model, '部分特殊主题布局采用近似显示')
        properties = dict(theme.get('centralTopic' if depth == 0 else 'mainTopic' if depth == 1 else 'subTopic', {}).get('properties', {}))
        if branch_color and depth > 0:
            properties.setdefault('svg:fill', branch_color)
            properties.setdefault('line-color', branch_color)
        properties.update(node.get('style', {}).get('properties', {}))
        appearance = xmind_style(properties)
        appearance.update(fill='#4bc48a' if depth == 0 else branch_color or '#ffcc45',
                          text_color='#ffffff' if depth == 0 else '#252525',
                          font_size=16 if depth == 0 else 14, font_family='Microsoft YaHei',
                          stroke_width=0, bold=False, italic=False, underline=False, align='left')
        explicit = xmind_style(node.get('style', {}).get('properties', {}))
        appearance.update(explicit)
        if is_summary:
            appearance['fill'] = explicit.get('fill', '#ed6a55')
            appearance['text_color'] = explicit.get('text_color', '#ffffff')
        shape_name = properties.get('shape-class', '').rsplit('.', 1)[-1]
        shape = {'roundedRect': 'rounded', 'rect': 'rectangle', 'ellipse': 'ellipse',
                 'diamond': 'diamond', 'underline': 'underline', 'noBorder': 'rectangle'}.get(shape_name, 'rounded')
        if shape_name == 'noBorder':
            appearance['stroke_width'] = 0
            appearance.setdefault('fill', 'transparent')
        shape = 'rounded'
        image_info = node.get('image', {})
        image = None
        if isinstance(image_info, dict) and image_info.get('src') in images:
            image = {'data': images[image_info['src']], 'width': number(image_info.get('width'), 200, 1, 2000),
                     'height': number(image_info.get('height'), 120, 1, 2000)}
        link = web_link(node.get('href'))
        if node.get('href') and not link:
            _warn(model, '非网页链接仅作原文件信息保留，不自动打开')
        width, height = topic_size(node.get('title', ''), appearance, node.get('customWidth'), image)
        if link:
            width = max(width, 100)
            height += 24
        position = node.get('nvPosition', {})
        if position and not theme and not node.get('style'):
            appearance = {}
        item = {'id': node.get('id') or identifier(), 'text': node.get('title', ''),
                'x': position.get('x', depth * 230), 'y': position.get('y', len(page['nodes']) * 100),
                'width': position.get('width', width), 'height': position.get('height', height),
                'shape': position.get('shape', shape), 'parent': parent,
                **({'style': appearance} if appearance else {}), **({'image': image} if image else {}),
                **({'link': link} if link else {})}
        page['nodes'].append(item)
        if parent and not is_summary:
            page['edges'].append({'id': identifier(), 'source': parent, 'target': item['id'], 'text': '', 'arrow': False,
                                  'style': {'stroke': branch_color or '#ffcc45',
                                            'stroke_width': 1,
                                            'route': 'orthogonal' if 'Elbow' in properties.get('line-class', '') else 'curve'}})
        children = node.get('children', {})
        if not isinstance(children, dict):
            raise ValueError('无效的 XMind 子主题。')
        if set(children) - {'attached', 'summary'}:
            _warn(model, '包含自由主题或特殊子主题结构')
        palette = ['#ffcc45', '#ed6a55', '#55b9d5']
        attached_ids = []
        for index, child in enumerate(children.get('attached', [])):
            child_color = palette[index % len(palette)] if depth == 0 and palette else branch_color
            attached_ids.append(topic(child, page, theme, item['id'], depth + 1, child_color))
        summary_nodes = {}
        for child in children.get('summary', []):
            summary_nodes[child.get('id')] = topic(child, page, theme, item['id'], depth + 1, branch_color, True)
        attached = children.get('attached', [])
        used = set()
        summaries = node.get('summaries', [])
        if not isinstance(summaries, list) or len(summaries) > MAX_NODES:
            raise ValueError('无效的 XMind 概要列表。')
        for summary in summaries:
            if not isinstance(summary, dict):
                raise ValueError('无效的 XMind 概要。')
            target = summary.get('topicId')
            match = re.fullmatch(r'\(\s*(\d+)\s*,\s*(\d+)\s*\)', str(summary.get('range', '')))
            if target not in summary_nodes or target in used or not match:
                _warn(model, '部分概要范围无效，概要文字单独显示')
                continue
            first, last = map(int, match.groups())
            if not 0 <= first <= last < len(attached):
                _warn(model, '部分概要范围无效，概要文字单独显示')
                continue
            used.add(target)
            page.setdefault('summaries', []).append({
                'id': summary.get('id') or identifier(), 'topic': summary_nodes[target],
                'topics': attached_ids[first:last + 1],
                'style': {'stroke': '#10113f', 'stroke_width': 2}})
        if set(summary_nodes) - used:
            _warn(model, '部分概要缺少有效范围，概要文字单独显示')
        return item['id']
    for sheet in sheets:
        if not isinstance(sheet, dict):
            raise ValueError('无效的 XMind 页面。')
        if set(sheet) - {'id', 'title', 'rootTopic', 'class'}:
            _warn(model, '页面包含关系、边界、概要、样式或其他特殊结构')
        page = {'id': sheet.get('id') or identifier(), 'name': sheet.get('title', '主题'),
                'kind': 'mindmap', 'nodes': [], 'edges': []}
        topic(sheet.get('rootTopic'), page, sheet.get('theme', {}))
        background = sheet.get('style', {}).get('properties', {}).get('svg:fill') or sheet.get('theme', {}).get('map', {}).get('properties', {}).get('svg:fill')
        if (background := color(background)) is not None:
            page['background'] = background
        def has_positions(node):
            return 'nvPosition' in node and all(has_positions(c) for c in node.get('children', {}).get('attached', []))
        if not has_positions(sheet['rootTopic']):
            layout_tree(page, sheet['rootTopic'].get('structureClass'))
        model['pages'].append(page)
    return model


def read_diagram(path):
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('图形文件超过 16 MiB 限制。')
    data = path.read_bytes()
    try:
        if path.suffix.lower() == '.nvdiagram':
            model = json.loads(data)
            if set(model) - {'version', 'type', 'pages', 'warnings', 'protected_paths'}:
                raise ValueError('自有格式含当前版本无法解释的信息。')
        elif path.suffix.lower() == '.drawio':
            model = _drawio(data)
        elif path.suffix.lower() == '.xmind':
            model = _xmind(data)
        else:
            raise ValueError('请选择 .nvdiagram、.drawio 或 .xmind 文件。')
        validate(model)
    except (TypeError, KeyError, AttributeError, RecursionError, UnicodeError) as error:
        raise ValueError('图形文件结构无效：' + str(error)) from error
    if model.get('warnings') and path.suffix.lower() != '.nvdiagram':
        paths = model.setdefault('protected_paths', [])
        resolved = str(path.resolve())
        if resolved not in paths:
            paths.append(resolved)
    model.setdefault('warnings', [])
    model.setdefault('protected_paths', [])
    return model


def _drawio_bytes(model):
    root = ET.Element('mxfile', {'host': 'NotepadView'})
    for page in model['pages']:
        diagram = ET.SubElement(root, 'diagram', {'id': page['id'], 'name': page['name'], 'nvKind': page['kind']})
        graph = ET.SubElement(diagram, 'mxGraphModel')
        cells = ET.SubElement(graph, 'root')
        ET.SubElement(cells, 'mxCell', {'id': '0'})
        ET.SubElement(cells, 'mxCell', {'id': '1', 'parent': '0'})
        # 外部 ID 可能与 draw.io 的保留图层 ID 冲突，写出时统一映射。
        mapping = {n['id']: 'n' + str(i) for i, n in enumerate(page['nodes'])}
        for node in page['nodes']:
            style = {'rectangle': '', 'rounded': 'rounded=1;', 'diamond': 'rhombus;'}[node['shape']]
            cell = ET.SubElement(cells, 'mxCell', {'id': mapping[node['id']], 'value': node['text'],
                'style': style + 'whiteSpace=wrap;html=0;', 'vertex': '1', 'parent': '1',
                **({'nvParent': mapping[node['parent']]} if node.get('parent') else {})})
            ET.SubElement(cell, 'mxGeometry', {**{k: str(node[k]) for k in ('x', 'y', 'width', 'height')}, 'as': 'geometry'})
        for i, edge in enumerate(page['edges']):
            cell = ET.SubElement(cells, 'mxCell', {'id': 'e' + str(i), 'value': edge.get('text', ''),
                'edge': '1', 'parent': '1', 'source': mapping[edge['source']], 'target': mapping[edge['target']],
                'style': 'endArrow=' + ('classic' if edge.get('arrow', True) else 'none') + ';',
                **({'nvBendX': str(edge['bend']['x']), 'nvBendY': str(edge['bend']['y'])} if edge.get('bend') else {})})
            geo = ET.SubElement(cell, 'mxGeometry', {'relative': '1', 'as': 'geometry'})
            if edge.get('bend'):
                points = ET.SubElement(geo, 'Array', {'as': 'points'})
                ET.SubElement(points, 'mxPoint', {k: str(v) for k, v in edge['bend'].items()})
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def _xmind_bytes(model):
    sheets = []
    for page in model['pages']:
        nodes = page['nodes']
        roots = [n for n in nodes if not n.get('parent')]
        expected = {(n['parent'], n['id']) for n in nodes if n.get('parent')}
        actual = [(e['source'], e['target']) for e in page['edges']]
        if len(roots) != 1 or set(actual) != expected or len(actual) != len(expected) or any(e.get('text') or e.get('bend') or not e.get('arrow', True) for e in page['edges']):
            raise ValueError('XMind 仅能保存单中心主题树；请另存自有格式或 draw.io，保留所有节点与连线。')
        def topic(node):
            result = {'id': node['id'], 'class': 'topic', 'title': node['text'],
                      'nvPosition': {k: node[k] for k in ('x', 'y', 'width', 'height', 'shape')}}
            children = [topic(n) for n in nodes if n.get('parent') == node['id']]
            if children:
                result['children'] = {'attached': children}
            return result
        sheets.append({'id': page['id'], 'class': 'sheet', 'title': page['name'], 'rootTopic': topic(roots[0])})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('content.json', json.dumps(sheets, ensure_ascii=False))
        archive.writestr('metadata.json', json.dumps({'creator': {'name': 'Notepad View', 'version': '1'}}))
        archive.writestr('manifest.json', json.dumps({'file-entries': {'content.json': {}, 'metadata.json': {}}}))
    return buffer.getvalue()


def write_diagram(path, model):
    path = Path(path).resolve()
    validate(model)
    if any(path == Path(original).resolve() for original in model.get('protected_paths', [])):
        raise ValueError('原文件包含无法无损写回的信息，已保护原文件。请另存自有格式或新文件。')
    suffix = path.suffix.lower()
    if suffix in ('.drawio', '.xmind') and any(
            'background' in page or page.get('summaries')
            or any('style' in n or 'image' in n or 'link' in n or n['shape'] in ('ellipse', 'underline') for n in page['nodes'])
            or any('style' in e or 'points' in e for e in page['edges']) for page in model['pages']):
        raise ValueError('外部格式写出暂不支持保留这些样式、图片或路径；请使用自有格式。')
    if suffix == '.nvdiagram':
        data = json.dumps(model, ensure_ascii=False, allow_nan=False, indent=2).encode('utf-8')
    elif suffix == '.drawio':
        data = _drawio_bytes(model)
    elif suffix == '.xmind':
        data = _xmind_bytes(model)
    else:
        raise ValueError('图形保存仅支持 .nvdiagram、.drawio、.xmind。')
    if len(data) > MAX_BYTES:
        raise ValueError('保存内容超过 16 MiB 限制。')
    output = QSaveFile(str(path))
    if not output.open(QIODevice.WriteOnly):
        raise OSError(output.errorString())
    if output.write(data) != len(data):
        output.cancelWriting()
        raise OSError(output.errorString())
    if not output.commit():
        raise OSError(output.errorString())
