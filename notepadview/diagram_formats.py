"""本地画布格式与保守的 draw.io / XMind 读写。"""
import base64
from copy import deepcopy
from html import unescape
import io
import json
import math
import re
from pathlib import Path
from urllib.parse import unquote
from uuid import uuid4
import xml.etree.ElementTree as ET
import zipfile
import zlib

from PySide6.QtCore import QIODevice, QSaveFile

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
        if set(page) - {'id', 'name', 'kind', 'nodes', 'edges'}:
            raise ValueError('页面包含未支持的属性。')
        page_ids.add(page['id'])
        if page.get('kind') not in ('mindmap', 'flowchart') or not isinstance(page.get('name'), str):
            raise ValueError('无效的页面类型或名称。')
        if not isinstance(page.get('nodes'), list) or not isinstance(page.get('edges'), list):
            raise ValueError('无效的节点或连线。')
        ids = set()
        for node in page['nodes']:
            if not isinstance(node, dict) or set(node) - {'id', 'text', 'x', 'y', 'width', 'height', 'shape', 'parent'}:
                raise ValueError('节点结构无效或包含未支持的属性。')
            count += 1
            if count > MAX_NODES:
                raise ValueError('节点超过 5000 个，无法打开。')
            if not isinstance(node.get('id'), str) or not node['id'] or node['id'] in ids:
                raise ValueError('节点 ID 无效或重复。')
            ids.add(node['id'])
            if not isinstance(node.get('text'), str) or len(node['text']) > 100000:
                raise ValueError('节点文本无效或过长。')
            if node.get('shape') not in ('rectangle', 'rounded', 'diamond'):
                raise ValueError('不支持的节点形状。')
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
        edge_ids = set(ids)
        if len(page['edges']) > MAX_NODES * 2:
            raise ValueError('连线数量过多。')
        for edge in page['edges']:
            if not isinstance(edge, dict) or set(edge) - {'id', 'source', 'target', 'text', 'arrow', 'bend'}:
                raise ValueError('连线结构无效或包含未支持的属性。')
            bend = edge.get('bend')
            if bend is not None and (not isinstance(bend, dict) or set(bend) != {'x', 'y'} or any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1000000 for v in bend.values())):
                raise ValueError('连线折点坐标无效。')
            if not isinstance(edge.get('id'), str) or edge['id'] in edge_ids:
                raise ValueError('连线 ID 无效或重复。')
            edge_ids.add(edge['id'])
            if edge.get('source') not in ids or edge.get('target') not in ids or not isinstance(edge.get('text', ''), str):
                raise ValueError('连线引用了不存在的节点。')
    return model


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
            _warn(model, '画布显示与布局设置无法完整保留')
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
            _unknown(model, cell, ('id', 'value', 'style', 'vertex', 'edge', 'parent', 'source', 'target', 'nvParent', 'nvBendX', 'nvBendY'), ('mxGeometry',), '图元')
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
                       'rhombus': ('',), 'shape': ('rhombus', 'rectangle'),
                       'edgeStyle': ('none',), 'endArrow': ('classic', 'none'),
                       'endFill': ('1',), 'orthogonalLoop': ('1',), 'jettySize': ('auto',)}
            if any(key not in allowed or value not in allowed[key] for key, value in style.items()):
                _warn(model, '存在复杂样式（颜色、字体、锚点或特殊图形等），首版仅显示基础样式')
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
                                 'rounded' if style.get('rounded') == '1' else 'rectangle'}
                for key, default in (('x', 0), ('y', 0), ('width', 150), ('height', 64)):
                    node[key] = float(geo.get(key, default))
                page['nodes'].append(node)
            elif cell.get('edge') == '1':
                if geo is not None:
                    _unknown(model, geo, ('relative', 'as'), ('Array',), '连线几何')
                    arrays = list(geo)
                    points = geo.findall('./Array/mxPoint')
                    if arrays:
                        if len(arrays) == 1 and arrays[0].attrib == {'as': 'points'} and len(points) == 1 and len(list(arrays[0])) == 1 and set(points[0].attrib) <= {'x', 'y'} and not list(points[0]):
                            cell.set('nvBendX', points[0].get('x', '0'))
                            cell.set('nvBendY', points[0].get('y', '0'))
                        else:
                            _warn(model, '复杂连线控制点未支持')
                if cell.get('parent', '1') != '1':
                    _warn(model, '嵌套连线未支持')
                page['edges'].append({'id': cell.get('id'), 'source': cell.get('source'),
                                      'target': cell.get('target'), 'text': text,
                                      'arrow': style.get('endArrow') != 'none',
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
    def topic(node, page, parent=None, depth=0):
        if depth > 200 or len(page['nodes']) >= MAX_NODES or not isinstance(node, dict):
            raise ValueError('主题层数或数量超过限制。')
        if set(node) - {'id', 'title', 'children', 'class', 'nvPosition', 'structureClass'}:
            _warn(model, '主题包含备注、标签、链接、标记或复杂样式等未支持信息')
        if node.get('structureClass') is not None:
            _warn(model, '特殊主题布局将转换为普通主题树')
        position = node.get('nvPosition', {})
        item = {'id': node.get('id') or identifier(), 'text': node.get('title', ''),
                'x': position.get('x', depth * 230), 'y': position.get('y', len(page['nodes']) * 100),
                'width': position.get('width', 150), 'height': position.get('height', 64),
                'shape': position.get('shape', 'rounded'), 'parent': parent}
        page['nodes'].append(item)
        if parent:
            page['edges'].append({'id': identifier(), 'source': parent, 'target': item['id'], 'text': '', 'arrow': True})
        children = node.get('children', {})
        if not isinstance(children, dict):
            raise ValueError('无效的 XMind 子主题。')
        if set(children) - {'attached'}:
            _warn(model, '包含自由主题、概要或特殊子主题结构')
        for child in children.get('attached', []):
            topic(child, page, item['id'], depth + 1)
    for sheet in sheets:
        if not isinstance(sheet, dict):
            raise ValueError('无效的 XMind 页面。')
        if set(sheet) - {'id', 'title', 'rootTopic', 'class'}:
            _warn(model, '页面包含关系、边界、概要、样式或其他特殊结构')
        page = {'id': sheet.get('id') or identifier(), 'name': sheet.get('title', '主题'),
                'kind': 'mindmap', 'nodes': [], 'edges': []}
        topic(sheet.get('rootTopic'), page)
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
    except (TypeError, KeyError, RecursionError, UnicodeError) as error:
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
