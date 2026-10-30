"""将外部图表的显示属性转换为本地只读画布样式。"""
import math
import re
import unicodedata


STYLE_KEYS = {'fill', 'stroke', 'text_color', 'font_family', 'font_size', 'bold',
              'italic', 'underline', 'stroke_width', 'dashed', 'align', 'route', 'tree_direction'}


def color(value, default=None):
    if value == 'none':
        return 'transparent'
    if not isinstance(value, str):
        return default
    if re.fullmatch(r'#[0-9a-fA-F]{8}', value):
        # XMind 的八位颜色顺序为 RGBA，Qt 使用 ARGB。
        return value[:7] if value[-2:].lower() == 'ff' else '#' + value[-2:] + value[1:7]
    return value if re.fullmatch(r'#[0-9a-fA-F]{6}|#[0-9a-fA-F]{3}', value) else default


def number(value, default, minimum=0, maximum=200):
    try:
        result = float(re.sub(r'(pt|px)$', '', str(value)))
        return min(maximum, max(minimum, result)) if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def xmind_style(properties):
    style = {}
    for source, target in [('svg:fill', 'fill'), ('border-line-color', 'stroke'), ('fo:color', 'text_color')]:
        if (value := color(properties.get(source))) is not None:
            style[target] = value
    if 'fo:font-family' in properties:
        style['font_family'] = str(properties['fo:font-family'])[:200]
    for source, target, default in [('fo:font-size', 'font_size', 14), ('border-line-width', 'stroke_width', 1)]:
        if source in properties:
            style[target] = number(properties[source], default)
    if 'fo:font-weight' in properties:
        style['bold'] = str(properties['fo:font-weight']) == 'bold' or number(properties['fo:font-weight'], 400, 0, 1000) >= 600
    if 'fo:font-style' in properties:
        style['italic'] = properties['fo:font-style'] == 'italic'
    if 'fo:text-decoration' in properties:
        style['underline'] = properties['fo:text-decoration'] == 'underline'
    style['align'] = properties.get('fo:text-align', 'left') if properties.get('fo:text-align') in ('left', 'center', 'right') else 'left'
    return style


def drawio_style(properties):
    style = {}
    for source, target in [('fillColor', 'fill'), ('strokeColor', 'stroke'), ('fontColor', 'text_color')]:
        if (value := color(properties.get(source))) is not None:
            style[target] = value
    for source, target, default in [('fontSize', 'font_size', 12), ('strokeWidth', 'stroke_width', 1)]:
        if source in properties:
            style[target] = number(properties[source], default)
    if 'font_size' in style:
        style['font_size'] *= 0.75  # draw.io 使用像素，Qt 字体使用磅。
    if 'fontFamily' in properties:
        style['font_family'] = properties['fontFamily'][:200]
    if 'fontStyle' in properties:
        bits = int(number(properties['fontStyle'], 0))
        style.update(bold=bool(bits & 1), italic=bool(bits & 2), underline=bool(bits & 4))
    if properties.get('align') in ('left', 'right', 'center'):
        style['align'] = properties['align']
    if 'dashed' in properties:
        style['dashed'] = properties['dashed'] == '1'
    if properties.get('edgeStyle') in ('orthogonalEdgeStyle', 'elbowEdgeStyle'):
        style['route'] = 'orthogonal'
    elif properties.get('curved') == '1':
        style['route'] = 'curve'
    return style


def text_document(text, style):
    from PySide6.QtGui import QFont, QTextDocument, QTextOption
    document = QTextDocument()
    document.setDocumentMargin(0)
    font = QFont(style.get('font_family', 'Microsoft YaHei'))
    font.setPointSizeF(max(1, style.get('font_size', 12)))
    font.setBold(style.get('bold', False))
    font.setItalic(style.get('italic', False))
    font.setUnderline(style.get('underline', False))
    document.setDefaultFont(font)
    option = document.defaultTextOption()
    option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
    document.setDefaultTextOption(option)
    document.setPlainText(text)
    return document


def topic_size(text, style, custom_width=None, image=None):
    from PySide6.QtGui import QGuiApplication
    document = text_document(text, style) if QGuiApplication.instance() else None
    size = max(6, style.get('font_size', 14)) * 4 / 3
    widths = [sum(1 if unicodedata.east_asian_width(c) in 'WF' else 0.6 for c in line) * size
              for line in text.split('\n')]
    natural = document.idealWidth() if document else max(widths, default=0)
    width = number(custom_width, min(420, max(40, natural + 24)), 40, 2000)
    if image:
        width = max(width, image['width'] + 24)
    if document:
        document.setTextWidth(width - 24)
        height = document.size().height() + 12
    else:
        count = sum(max(1, math.ceil(w / max(1, width - 24))) for w in widths)
        height = count * size * 1.25 + 12
    if image:
        height += image['height'] + 12
    return min(10000, width), min(10000, max(24, height))


def summary_bounds(page, summary):
    nodes = {n['id']: n for n in page['nodes']}
    selected = set(summary['topics'])
    members = []
    for node in page['nodes']:
        current = node
        while current is not None:
            if current['id'] in selected:
                members.append(node)
                break
            current = nodes.get(current.get('parent'))
    return (min(n['x'] for n in members), min(n['y'] for n in members),
            max(n['x'] + n['width'] for n in members), max(n['y'] + n['height'] for n in members))


def layout_tree(page, structure):
    nodes = {n['id']: n for n in page['nodes']}
    summaries = page.get('summaries', [])
    summary_ids = {s['topic'] for s in summaries}
    children = {key: [] for key in nodes}
    groups = {key: [] for key in nodes}
    for summary in summaries:
        groups[nodes[summary['topic']]['parent']].append(summary)
    for node in page['nodes']:
        if node['parent'] in children and node['id'] not in summary_ids:
            children[node['parent']].append(node)
    root = page['nodes'][0]
    down = structure in ('org.xmind.ui.org-chart.down', 'org.xmind.ui.tree.down')
    left = structure in ('org.xmind.ui.logic.left', 'org.xmind.ui.tree.left')
    split = structure in ('org.xmind.ui.map.unbalanced', 'org.xmind.ui.map.clockwise', 'org.xmind.ui.map') or not structure
    cross = 'width' if down else 'height'
    extent = {}

    def measure(node):
        for child in children[node['id']]:
            measure(child)
        for summary in groups[node['id']]:
            required = measure(nodes[summary['topic']])
            covered = sum(extent[t] for t in summary['topics']) + (len(summary['topics']) - 1) * 12
            extra = max(0, required - covered)
            extent[summary['topics'][0]] += extra / 2
            extent[summary['topics'][-1]] += extra / 2
        extent[node['id']] = max(node[cross], sum(extent[c['id']] for c in children[node['id']]) + max(0, len(children[node['id']]) - 1) * 12)
        return extent[node['id']]

    measure(root)
    for edge in page['edges']:
        target = nodes.get(edge['target'])
        if target and target.get('parent') == edge['source']:
            edge.setdefault('style', {})['tree_direction'] = 'down' if down else 'horizontal'

    def place_summaries(node, direction):
        occupied = []
        for summary in groups[node['id']]:
            target = nodes[summary['topic']]
            x1, y1, x2, y2 = summary_bounds(page, summary)
            if down:
                along = max([y2] + [b[3] for b in occupied]) + 48
                offset = (x1 + x2 - extent[target['id']]) / 2
            else:
                along = (max([x2] + [b[2] for b in occupied]) + 48 if direction > 0
                         else min([x1] + [b[0] for b in occupied]) - 48)
                offset = (y1 + y2 - extent[target['id']]) / 2
            place(target, along, offset, direction)
            occupied.append(summary_bounds(page, {'topics': [target['id']]}))

    def place(node, along, offset, direction):
        if down:
            node['x'], node['y'] = offset + (extent[node['id']] - node['width']) / 2, along
        else:
            node['x'] = along if direction > 0 else along - node['width']
            node['y'] = offset + (extent[node['id']] - node['height']) / 2
        occupied = sum(extent[c['id']] for c in children[node['id']]) + max(0, len(children[node['id']]) - 1) * 12
        cursor = offset + (extent[node['id']] - occupied) / 2
        for child in children[node['id']]:
            next_along = node['y'] + node['height'] + 28 if down else node['x'] + node['width'] + 28 if direction > 0 else node['x'] - 28
            place(child, next_along, cursor, direction)
            cursor += extent[child['id']] + 12
        place_summaries(node, direction)

    if split and not down:
        root['x'], root['y'] = 0, 0
        branches = children[root['id']]
        for direction, group in [(1, branches[:math.ceil(len(branches) / 2)]), (-1, branches[math.ceil(len(branches) / 2):])]:
            cursor = root['height'] / 2 - (sum(extent[c['id']] for c in group) + max(0, len(group) - 1) * 12) / 2
            for child in group:
                place(child, root['width'] + 28 if direction > 0 else -28, cursor, direction)
                cursor += extent[child['id']] + 12
        place_summaries(root, 1)
    else:
        place(root, 0, 0, -1 if left else 1)
