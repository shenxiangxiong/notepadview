"""PDF 内容操作；坐标统一使用未旋转页面的 PDF 点。"""
import base64
from contextlib import nullcontext
from functools import wraps, lru_cache
from copy import deepcopy
from pathlib import Path
import math
import hashlib
import struct
from difflib import SequenceMatcher

import pymupdf as fitz
from PySide6.QtGui import QColor


def color(value):
    result = QColor(value)
    if not result.isValid():
        raise ValueError('颜色无效。')
    return result.redF(), result.greenF(), result.blueF()


def checked_rect(value):
    rect = fitz.Rect(value)
    if not all(math.isfinite(n) for n in rect) or rect.is_empty or rect.is_infinite:
        raise ValueError('编辑区域必须是有效矩形。')
    return rect


def insert_text(page, rect, text, fill, family, size, grow=False):
    if not text.strip():
        raise ValueError('文字不能为空。')
    if not 1 <= float(size) <= 300:
        raise ValueError('字号必须在 1–300 pt 之间。')
    # 从系统字体文件嵌入，避免将任意字体名称当作 PDF 内建字体。
    aliases = {
        'helvetica': 'helv', 'helvetica-bold': 'hebo',
        'helvetica-oblique': 'heit', 'helvetica-boldoblique': 'hebi',
        'times-roman': 'tiro', 'times-bold': 'tibo',
        'times-italic': 'tiit', 'times-bolditalic': 'tibi',
        'courier': 'cour', 'courier-bold': 'cobo',
        'courier-oblique': 'coit', 'courier-boldoblique': 'cobi',
    }
    normalized = family.split('+')[-1].casefold()
    font = 'china-s' if any(ord(c) > 255 for c in text) else aliases.get(normalized, 'helv')
    font_path = None
    if Path('C:\\Windows\\Fonts').is_dir():
        resolved = family.casefold()
        import winreg
        for key_name in (r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts',):
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_name) as key:
                for index in range(winreg.QueryInfoKey(key)[1]):
                    name, filename, _ = winreg.EnumValue(key, index)
                    if name.split(' (')[0].casefold() == resolved:
                        candidate = Path(filename)
                        if not candidate.is_absolute():
                            candidate = Path('C:\\Windows\\Fonts') / candidate
                        if candidate.suffix.lower() in ('.ttf', '.otf') and candidate.is_file():
                            font_path = str(candidate)
                            break
    if font_path:
        font = 'nv' + hashlib.sha256(font_path.encode()).hexdigest()[:12]
        page.insert_font(fontname=font, fontfile=font_path)
    rect = fitz.Rect(rect)
    bounds = page.cropbox
    if grow:
        metrics = fitz.Font(fontfile=font_path) if font_path else fitz.Font(fontname=font)
        width = max(metrics.text_length(line, fontsize=float(size)) for line in text.split('\n'))
        rect.x1 = min(bounds.width, max(rect.x1, rect.x0 + width + 1))
    while True:
        shape = page.new_shape()
        result = shape.insert_textbox(rect, text, fontsize=float(size), fontname=font,
                                     fontfile=font_path, color=color(fill))
        if result >= 0:
            shape.commit(overlay=True)
            return rect
        if not grow:
            raise ValueError('文字超出文本框，请扩大区域或减小字号。')
        height = min(bounds.height, rect.y1 + max(-result + 1, float(size)))
        if height > rect.y1:
            rect.y1 = height
        elif rect.x1 < bounds.width:
            rect.x1 = bounds.width
        else:
            raise ValueError('文字超出页面可用区域，请缩短内容或减小字号。')


@lru_cache(maxsize=256)
def font_names(data):
    """读取 TrueType/OpenType 名称表，兼容 PDF 与文本提取使用不同名称。"""
    names = set()
    try:
        count = struct.unpack_from('>H', data, 4)[0]
        for i in range(count):
            tag, _, offset, _ = struct.unpack_from('>4sIII', data, 12 + i * 16)
            if tag != b'name':
                continue
            _, records, strings = struct.unpack_from('>HHH', data, offset)
            for j in range(records):
                platform, _, _, name_id, length, start = struct.unpack_from(
                    '>HHHHHH', data, offset + 6 + j * 12)
                if name_id not in (1, 4, 6):
                    continue
                raw = data[offset + strings + start:offset + strings + start + length]
                names.add(raw.decode('utf-16-be' if platform in (0, 3) else 'mac_roman'))
    except (struct.error, UnicodeError):
        pass
    return names


@lru_cache(maxsize=256)
def system_font_data(path):
    return Path(path).read_bytes()


def replacement_font(page, span, text):
    def normalized(name):
        return ''.join(c for c in name.split('+')[-1].casefold() if c.isalnum())

    wanted = normalized(span['font'])
    for xref, _, _, base, resource, *_ in page.get_fonts(full=True):
        data = page.parent.extract_font(xref)[3] if xref else b''
        names = {base} | font_names(data)
        if wanted not in {normalized(name) for name in names}:
            continue
        if data:
            font = fitz.Font(fontbuffer=data)
            if any(not font.has_glyph(ord(c)) for c in text if not c.isspace()):
                continue
        elif any(ord(c) > 255 for c in text) and resource not in ('china-s', 'china-t', 'japan', 'korea'):
            continue
        return resource
    # 嵌入的子集缺字时，优先从系统找到同款完整字体。
    directory = Path(r'C:\Windows\Fonts')
    if directory.is_dir():
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts') as key:
            for index in range(winreg.QueryInfoKey(key)[1]):
                _, filename, _ = winreg.EnumValue(key, index)
                path = Path(filename)
                if not path.is_absolute():
                    path = directory / path
                if path.suffix.lower() not in ('.ttf', '.otf') or not path.is_file():
                    continue
                data = system_font_data(str(path))
                if wanted not in {normalized(name) for name in font_names(data)}:
                    continue
                font = fitz.Font(fontbuffer=data)
                if all(font.has_glyph(ord(c)) for c in text if not c.isspace()):
                    resource = 'nv' + hashlib.sha256(data).hexdigest()[:12]
                    page.insert_font(fontname=resource, fontbuffer=data)
                    return resource
    bold, italic = bool(span.get('flags', 0) & 16), bool(span.get('flags', 0) & 2)
    if any(ord(c) > 255 for c in text):
        return 'china-s'
    return {(False, False): 'helv', (True, False): 'hebo',
            (False, True): 'heit', (True, True): 'hebi'}[bold, italic]


def mutation(method):
    @wraps(method)
    def apply(self, *args, **kwargs):
        if method.__name__ == 'insert_pages':
            path = args[1] if len(args) > 1 else kwargs.get('path')
            if path is not None:
                position = args[0] if args else kwargs['position']
                return self.insert_data(position, base64.b64encode(Path(path).read_bytes()).decode('ascii'))
        before = self.checkpoint()
        try:
            result = method(self, *args, **kwargs)
        except Exception:
            if self._replaying:
                raise
            self._position = -1
            self.restore_checkpoint(before)
            raise
        if not self._replaying:
            self.operations = self.operations[:self._position]
            self.operations.append({'method': method.__name__, 'args': deepcopy(args), 'kwargs': deepcopy(kwargs)})
            self._position += 1
        return result
    return apply


class PdfModel:
    def __init__(self, data, layers=None):
        self._data = bytes(data)
        self._doc = fitz.open(stream=self._data, filetype='pdf')
        self.operations = []
        self._position = 0
        self._replaying = False
        self.layers = deepcopy(layers or [])
        self._initial_layers = deepcopy(self.layers)
        with self.open() as doc:
            if doc.needs_pass or not doc.page_count:
                raise ValueError('不支持需要密码或没有页面的 PDF。')
            if doc.is_repaired:
                raise ValueError('PDF 结构损坏，请先修复文件。')
            if doc.permissions >= 0 and not doc.permissions & fitz.PDF_PERM_MODIFY:
                raise ValueError('PDF 权限不允许修改。')

    @classmethod
    def from_path(cls, path):
        try:
            return cls(Path(path).read_bytes())
        except (RuntimeError, fitz.FileDataError) as error:
            raise ValueError('无法读取 PDF。') from error

    def open(self):
        # Borrowed UI-thread document: callers must not close or share it.
        return nullcontext(self._doc)

    @property
    def data(self):
        return self._doc.tobytes(no_new_id=True)

    @data.setter
    def data(self, value):
        self._doc.close()
        self._data = bytes(value)
        self._doc = fitz.open(stream=self._data, filetype='pdf')
        self._initial_layers = deepcopy(self.layers)
        self.operations = []
        self._position = 0
        self._replaying = False

    def close(self):
        if not self._doc.is_closed:
            self._doc.close()

    def checkpoint(self):
        return {'position': self._position, 'layers': deepcopy(self.layers)}

    def restore_checkpoint(self, state):
        position = state['position']
        if position == self._position and self.layers == state['layers']:
            return
        layer_only = {'add_text', 'update_layer', 'remove_layer'}
        changed = self.operations[min(position, self._position):max(position, self._position)]
        if self._position >= 0 and all(op['method'] in layer_only for op in changed):
            self._position = position
            self.layers = deepcopy(state['layers'])
            return
        self._doc.close()
        self._doc = fitz.open(stream=self._data, filetype='pdf')
        self.layers = deepcopy(self._initial_layers)
        self._replaying = True
        try:
            for operation in self.operations[:position]:
                getattr(self, operation['method'])(*operation['args'], **operation['kwargs'])
        finally:
            self._replaying = False
        self._position = position
        self.layers = deepcopy(state['layers'])

    def replay_operation(self, operation):
        self._restoring_history = True
        try:
            return getattr(self, operation['method'])(*operation['args'], **operation['kwargs'])
        finally:
            self._restoring_history = False

    def snapshot(self):
        return {'data': base64.b64encode(self.data).decode('ascii'), 'layers': deepcopy(self.layers)}

    @classmethod
    def from_snapshot(cls, state):
        return cls(base64.b64decode(state['data'], validate=True), state.get('layers', []))

    @property
    def page_count(self):
        with self.open() as doc:
            return doc.page_count

    def rotation(self, page):
        with self.open() as doc:
            return doc[page].rotation

    def to_page(self, page, point):
        with self.open() as doc:
            result = fitz.Point(point) * doc[page].derotation_matrix
            return result.x, result.y

    def to_display(self, page, point):
        with self.open() as doc:
            result = fitz.Point(point) * doc[page].rotation_matrix
            return result.x, result.y

    def display_rect(self, page, rect):
        with self.open() as doc:
            return list(fitz.Rect(rect) * doc[page].rotation_matrix)

    def page_rect(self, page, rect):
        with self.open() as doc:
            return list(fitz.Rect(rect) * doc[page].derotation_matrix)

    def _layer_rect(self, page, value):
        rect = checked_rect(value)
        if not self._replaying and not getattr(self, '_restoring_history', False):
            bounds = self._doc[page].cropbox
            if not (0 <= rect.x0 < bounds.width and 0 <= rect.y0 < bounds.height):
                raise ValueError('请将文字放在页面内。')
            rect.x1 = min(rect.x1, bounds.width)
            rect.y1 = min(rect.y1, bounds.height)
        return rect

    @mutation
    def add_text(self, page, rect, text, fill='#222222', family='Arial', size=16):
        rect = self._layer_rect(page, rect)
        with fitz.open() as doc:
            source = self._doc[page]
            target = doc.new_page(width=source.cropbox.width, height=source.cropbox.height)
            rect = insert_text(target, rect, text, fill, family, size, grow=True)
        self.layers.append({'page': page, 'rect': list(rect), 'text': text,
                            'fill': fill, 'font_family': family, 'font_size': size})

    @mutation
    def update_layer(self, index, **values):
        layer = deepcopy(self.layers[index])
        layer.update(values)
        with fitz.open() as doc:
            source = self._doc[layer['page']]
            target = doc.new_page(width=source.cropbox.width, height=source.cropbox.height)
            rect = insert_text(target, self._layer_rect(layer['page'], layer['rect']), layer['text'],
                               layer['fill'], layer['font_family'], layer['font_size'], grow=True)
            layer['rect'] = list(rect)
        self.layers[index] = layer

    @mutation
    def annotate(self, page, kind, rect, fill='#ffff00'):
        with self.open() as doc:
            target = doc[page]
            rect = checked_rect(rect)
            if kind == 'highlight':
                annot = target.add_highlight_annot(rect)
            elif kind == 'underline':
                annot = target.add_underline_annot(rect)
            elif kind == 'rect':
                annot = target.add_rect_annot(rect)
            else:
                raise ValueError('不支持的标注类型。')
            annot.set_colors(stroke=color(fill))
            annot.update()


    def text_details(self, page):
        blocks = self._doc[page].get_text('dict', flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)['blocks']
        result = []
        for block in blocks:
            if block['type'] != 0:
                continue
            groups = []
            for line in block['lines']:
                bounds = fitz.Rect(line['bbox'])
                if groups and any((bounds & fitz.Rect(other['bbox'])).height > min(bounds.height, fitz.Rect(other['bbox']).height) * 0.5
                                  for other in groups[-1]):
                    groups.append([])
                if not groups:
                    groups.append([])
                groups[-1].append(line)
            for lines in groups:
                bounds = fitz.Rect(lines[0]['bbox'])
                for line in lines[1:]:
                    bounds |= fitz.Rect(line['bbox'])
                result.append(dict(block, bbox=tuple(bounds), lines=lines))
        return result

    def text_blocks(self, page):
        return [{'id': i, 'rect': list(b['bbox']),
                 'text': '\n'.join(''.join(s['text'] for s in line['spans']) for line in b['lines'])}
                for i, b in enumerate(self.text_details(page))]

    def text_character_keys(self, page, block_id):
        block = self.text_details(page)[block_id]
        spans = {(tuple(s['origin']), tuple(s['bbox'])) for line in block['lines'] for s in line['spans']}
        raw = self._doc[page].get_text('rawdict', flags=fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_PRESERVE_IMAGES)
        return {(ord(c['c']), tuple(c['origin'])) for b in raw['blocks'] if b['type'] == 0
                for line in b['lines'] for span in line['spans']
                if (tuple(span['origin']), tuple(span['bbox'])) in spans for c in span['chars']}

    def render_text_block(self, page, block_id):
        source = self._doc[page]
        block = self.text_details(page)[block_id]
        keys = self.text_character_keys(page, block_id)
        with fitz.open() as doc:
            target = doc.new_page(width=source.cropbox.width, height=source.cropbox.height)
            target.set_rotation(source.rotation)
            for trace in source.get_texttrace():
                text = ''.join(chr(c[0]) for c in trace['chars'])
                if not text or not any((c[0], tuple(c[2])) in keys for c in trace['chars']):
                    continue
                resource = replacement_font(source, {'font': trace['font']}, text)
                entry = next((f for f in source.get_fonts() if f[4] == resource), None)
                data = self._doc.extract_font(entry[0])[3] if entry else b''
                if data:
                    target.insert_font(fontname=resource, fontbuffer=data)
                for char in trace['chars']:
                    if (char[0], tuple(char[2])) not in keys:
                        continue
                    target.insert_text(char[2], chr(char[0]), fontname=resource, fontsize=trace['size'],
                                       color=trace['color'], fill=trace['color'],
                                       render_mode=trace['type'] if trace['type'] < 3 else 3,
                                       border_width=trace['linewidth'],
                                       fill_opacity=trace['opacity'], stroke_opacity=trace['opacity'])
            display = fitz.Rect(block['bbox']) * target.rotation_matrix
            return target.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), clip=display, alpha=True)

    def remove_text_block(self, page, block_id):
        target = self._doc[page]
        selected = self.text_details(page)[block_id]
        keys = self.text_character_keys(page, block_id)
        traces = target.get_texttrace()
        affected = []
        regions = [fitz.Rect(selected['bbox'])]
        pending = list(traces)
        while pending:
            remaining = []
            for trace in pending:
                if any((fitz.Rect(char[3]) & region).get_area() > 0 for char in trace['chars'] for region in regions):
                    affected.append(trace)
                    regions.extend(fitz.Rect(char[3]) for char in trace['chars'])
                else:
                    remaining.append(trace)
            if len(remaining) == len(pending):
                break
            pending = remaining
        fonts = {}
        resources = {}
        for trace in affected:
            text = ''.join(chr(c[0]) for c in trace['chars'])
            if not text or all((c[0], tuple(c[2])) in keys for c in trace['chars']):
                continue
            if trace['font'] in resources:
                fonts[id(trace)] = resources[trace['font']]
                continue
            span = dict(font=trace['font'], origin=trace['chars'][0][2])
            entry = next((f for f in target.get_fonts() if f[3].split('+')[-1] == trace['font'].split('+')[-1]), None)
            resource = entry[4] if entry else replacement_font(target, span, text)
            entry = entry or next((f for f in target.get_fonts() if f[4] == resource), None)
            fonts[id(trace)] = (resource, self._doc.extract_font(entry[0])[3] if entry else b'')
            resources[trace['font']] = fonts[id(trace)]
        for region in regions:
            target.add_redact_annot(region, fill=False)
        target.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                                graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                                text=fitz.PDF_REDACT_TEXT_REMOVE)
        for trace in affected:
            text = ''.join(chr(c[0]) for c in trace['chars'])
            if not text or id(trace) not in fonts:
                continue
            resource, data = fonts[id(trace)]
            if data:
                target.insert_font(fontname=resource, fontbuffer=data)
            direction = trace['dir']
            matrix = fitz.Matrix(direction[0], -direction[1], direction[1], direction[0], 0, 0)
            shape = target.new_shape()
            for char in trace['chars']:
                if (char[0], tuple(char[2])) in keys:
                    continue
                origin = fitz.Point(char[2])
                shape.insert_text(origin, chr(char[0]), fontname=resource, fontsize=trace['size'],
                                   color=trace['color'], fill=trace['color'],
                                   render_mode=trace['type'] if trace['type'] < 3 else 3,
                                   border_width=trace['linewidth'],
                                   fill_opacity=trace['opacity'], stroke_opacity=trace['opacity'],
                                   morph=(origin, matrix))
            shape.commit()

    @mutation
    def replace_text(self, page, block_id, text, fill=None, family=None, size=None, offset=None):
        block = next((b for b in self.text_blocks(page) if b['id'] == block_id), None)
        if block is None:
            raise ValueError('文字块已变化，请重新选择。')
        rect = checked_rect(block['rect'])
        restoring = self._replaying or getattr(self, '_restoring_history', False)
        neighbors = [fitz.Rect(b['rect']) for b in self.text_blocks(page) if b['id'] != block_id]
        with self.open() as doc:
            target = doc[page]
            if any(a.type[0] == fitz.PDF_ANNOT_REDACT for a in target.annots() or []):
                raise ValueError('此页有尚未应用的删改标记，请先处理后再替换原文。')
            original = self.text_details(page)[block_id]
            spans = [s for line in original['lines'] for s in line['spans']]
            preserve = family is None and size is None
            if text and preserve and any(line['dir'] != (1.0, 0.0) for line in original['lines']):
                raise ValueError('暂不支持此文字方向的安全编辑。')
            styled_lines = []
            if preserve and text:
                source = []
                styles = []
                for line_index, line in enumerate(original['lines']):
                    if line_index:
                        source.append('\n')
                        styles.append(styles[-1])
                    for span in line['spans']:
                        source.extend(span['text'])
                        styles.extend([span] * len(span['text']))
                new_styles = []
                for tag, i, j, a, b in SequenceMatcher(None, ''.join(source), text, autojunk=False).get_opcodes():
                    if tag == 'equal':
                        new_styles.extend(styles[i:j])
                    else:
                        inherited = styles[min(i, len(styles) - 1):max(i + 1, j)]
                        new_styles.extend([inherited[min(len(inherited) - 1, n * len(inherited) // max(1, b - a))]
                                           for n in range(b - a)])
                cursor = 0
                for line_index, line_text in enumerate(text.split('\n')):
                    line = original['lines'][min(line_index, len(original['lines']) - 1)]
                    runs = []
                    for char, span in zip(line_text, new_styles[cursor:cursor + len(line_text)]):
                        if runs and runs[-1][0] is span:
                            runs[-1][1] += char
                        else:
                            runs.append([span, char])
                    if line_index >= len(original['lines']):
                        line = deepcopy(line)
                        step = max(s['size'] for s in spans) * 1.4
                        line['spans'][0]['origin'] = (line['spans'][0]['origin'][0],
                            line['spans'][0]['origin'][1] + step * (line_index - len(original['lines']) + 1))
                    styled_lines.append((line, runs))
                    cursor += len(line_text) + 1
            traces = target.get_texttrace() if preserve else []
            render_styles = {}
            for span in spans:
                matching = [trace for trace in traces if trace['chars']
                            and abs(trace['chars'][0][2][0] - span['origin'][0]) < 0.1
                            and abs(trace['chars'][0][2][1] - span['origin'][1]) < 0.1]
                filled = next((t for t in matching if t['type'] == 0), None)
                stroked = next((t for t in matching if t['type'] == 1), None)
                render_styles[id(span)] = dict(
                    render_mode=2 if filled and stroked else 1 if stroked else 0,
                    fill_opacity=filled['opacity'] if filled else 1,
                    stroke_opacity=stroked['opacity'] if stroked else 1,
                    border_width=stroked['linewidth'] if stroked else 0.05,
                    color=color(fill) if fill is not None else stroked['color'] if stroked else fitz.sRGB_to_pdf(span['color']))
            fonts = {}
            for _, runs in styled_lines:
                for span, run in runs:
                    fonts[(id(span), run)] = replacement_font(target, span, run)
            font_buffers = {}
            for resource in set(fonts.values()):
                entry = next((f for f in target.get_fonts() if f[4] == resource), None)
                if entry:
                    font_buffers[resource] = doc.extract_font(entry[0])[3]
            self.remove_text_block(page, block_id)
            if not text:
                return
            previous_traces = {(t['font'], t['size'], tuple(t['bbox']), t['chars']) for t in target.get_texttrace()}
            if preserve:
                for line, runs in styled_lines:
                    x, y = line['spans'][0]['origin']
                    if offset:
                        x += offset[0]
                        y += offset[1]
                    for span, run in runs:
                        if run == span['text'] and any(line is original_line for original_line in original['lines']):
                            x, y = span['origin']
                            if offset:
                                x += offset[0]
                                y += offset[1]
                        font = fonts[(id(span), run)]
                        ink = color(fill) if fill is not None else fitz.sRGB_to_pdf(span['color'])
                        data = font_buffers.get(font, b'')
                        if data:
                            target.insert_font(fontname=font, fontbuffer=data)
                        metrics = fitz.Font(fontbuffer=data) if data else fitz.Font(fontname=font)
                        width = metrics.text_length(run, fontsize=span['size'])
                        if not restoring and y > target.cropbox.height - 4:
                            raise ValueError('文字超出页面，请缩短内容或减小字号。')
                        if not restoring and not offset and x + width > target.cropbox.width - 4:
                            for char in run:
                                advance = metrics.text_length(char, fontsize=span['size'])
                                if x + advance > target.cropbox.width - 4:
                                    x = rect.x0
                                    y += span['size'] * 1.4
                                if y > target.cropbox.height - 4:
                                    raise ValueError('文字超出页面，请缩短内容或减小字号。')
                                target.insert_text((x, y), char, fontname=font, fontsize=span['size'],
                                                   fill=ink, **render_styles[id(span)])
                                x += advance
                            continue
                        if not restoring and offset and (x < 0 or y < 0 or x + width > target.cropbox.width or y > target.cropbox.height):
                            raise ValueError('请将文字放在页面内。')
                        target.insert_text((x, y), run, fontname=font, fontsize=span['size'],
                                           fill=ink, **render_styles[id(span)])
                        x += width
            else:
                span = spans[0]
                ink = fill if fill is not None else '#%06x' % span['color']
                insert_text(target, rect + (0, -1, 0, 2), text, ink,
                            family or span['font'], size if size is not None else span['size'], grow=True)
            if not preserve and not restoring:
                for trace in target.get_texttrace():
                    if (trace['font'], trace['size'], tuple(trace['bbox']), trace['chars']) in previous_traces:
                        continue
                    if any((fitz.Rect(trace['bbox']) & neighbor).get_area() > 0.1 for neighbor in neighbors):
                        raise ValueError('修改后文字与其他文字重叠，已撤销本次修改。')

    def fields(self, page):
        with self.open() as doc:
            return [{'id': w.xref, 'name': w.field_label or w.field_name,
                     'kind': w.field_type, 'value': w.field_value, 'rect': list(w.rect),
                     'choices': list(w.choice_values or []), 'flags': w.field_flags,
                     'on': w.on_state() if w.field_type in (fitz.PDF_WIDGET_TYPE_CHECKBOX, fitz.PDF_WIDGET_TYPE_RADIOBUTTON) else None}
                    for w in doc[page].widgets() or []]

    @mutation
    def set_field(self, page, field_id, value):
        with self.open() as doc:
            target = doc[page]
            widget = next((w for w in target.widgets() or [] if w.xref == field_id), None)
            if widget is None or widget.field_flags & fitz.PDF_FIELD_IS_READ_ONLY:
                raise ValueError('表单字段不存在或为只读。')
            if widget.field_type in (fitz.PDF_WIDGET_TYPE_CHECKBOX, fitz.PDF_WIDGET_TYPE_RADIOBUTTON):
                value = widget.on_state() if value else 'Off'
            elif widget.field_type in (fitz.PDF_WIDGET_TYPE_COMBOBOX, fitz.PDF_WIDGET_TYPE_LISTBOX):
                if widget.field_flags & fitz.PDF_CH_FIELD_IS_MULTI_SELECT:
                    raise ValueError('暂不支持多选列表。')
                choices = widget.choice_values or []
                pairs = [(c[0], c[1]) if isinstance(c, (tuple, list)) else (c, c) for c in choices]
                match = next((export for export, label in pairs if value in (export, label)), None)
                if match is not None:
                    value = match
                elif not widget.field_flags & fitz.PDF_CH_FIELD_IS_EDIT:
                    raise ValueError('请选择表单提供的选项。')
            elif widget.field_type != fitz.PDF_WIDGET_TYPE_TEXT:
                raise ValueError('不支持签名、按钮或此类复杂表单。')
            widget.field_value = value
            widget.update()


    @mutation
    def rotate_page(self, page, degrees):
        if degrees % 90:
            raise ValueError('旋转必须为 90 度的倍数。')
        with self.open() as doc:
            target = doc[page]
            target.set_rotation((target.rotation + degrees) % 360)


    @mutation
    def delete_page(self, page):
        with self.open() as doc:
            if doc.page_count <= 1:
                raise ValueError('必须保留至少一页。')
            doc.delete_page(page)

        self.layers = [dict(layer, page=layer['page'] - (layer['page'] > page))
                       for layer in self.layers if layer['page'] != page]

    @mutation
    def move_page(self, page, destination):
        count = self.page_count
        if not 0 <= destination < count:
            raise ValueError('目标页码无效。')
        if page == destination:
            return
        with self.open() as doc:
            before = destination + (destination > page)
            doc.move_page(page, to=-1 if before == count else before)

        for layer in self.layers:
            old = layer['page']
            if old == page:
                layer['page'] = destination
            elif page < old <= destination:
                layer['page'] -= 1
            elif destination <= old < page:
                layer['page'] += 1

    @mutation
    def insert_pages(self, position, path=None):
        if not 0 <= position <= self.page_count:
            raise ValueError('插入位置无效。')
        with self.open() as doc:
            if path is None:
                doc.new_page(pno=position)
                count = 1
            else:
                with fitz.open(path) as source:
                    if source.needs_pass:
                        raise ValueError('不能插入加密 PDF。')
                    count = source.page_count
                    doc.insert_pdf(source, start_at=position, widgets=True)

        for layer in self.layers:
            if layer['page'] >= position:
                layer['page'] += count

    @mutation
    def restore_state(self, state):
        replacement = fitz.open(stream=base64.b64decode(state['data'], validate=True), filetype='pdf')
        self._doc.close()
        self._doc = replacement
        self.layers = deepcopy(state.get('layers', []))

    @mutation
    def remove_layer(self, index):
        self.layers.pop(index)

    @mutation
    def remove_annotation(self, page, index):
        target = self._doc[page]
        target.delete_annot(target.load_annot(index))

    @mutation
    def insert_data(self, position, data):
        if not 0 <= position <= self.page_count:
            raise ValueError('插入位置无效。')
        with fitz.open(stream=base64.b64decode(data, validate=True), filetype='pdf') as source:
            if source.needs_pass or not source.page_count:
                raise ValueError('不能插入加密或空 PDF。')
            count = source.page_count
            self._doc.insert_pdf(source, start_at=position, widgets=True)
        for layer in self.layers:
            if layer['page'] >= position:
                layer['page'] += count

    def export(self):
        with fitz.open(stream=self.data, filetype='pdf') as doc:
            for layer in self.layers:
                insert_text(doc[layer['page']], checked_rect(layer['rect']), layer['text'],
                            layer['fill'], layer['font_family'], layer['font_size'])
            return doc.tobytes(garbage=3, deflate=True)

    def render_layer(self, index):
        layer = self.layers[index]
        source = self._doc[layer['page']]
        with fitz.open() as scratch:
            target = scratch.new_page(width=source.cropbox.width, height=source.cropbox.height)
            target.set_rotation(source.rotation)
            insert_text(target, layer['rect'], layer['text'], layer['fill'],
                        layer['font_family'], layer['font_size'])
            rect = fitz.Rect(layer['rect']) * target.rotation_matrix
            return target.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), clip=rect, alpha=True)

    def render(self, page, exclude_layer=None):
        target = self._doc[page]
        # Copy only the visible page when overlays must be composed.
        layers = [layer for index, layer in enumerate(self.layers)
                  if layer['page'] == page and index != exclude_layer]
        scratch = None
        try:
            if layers:
                scratch = fitz.open()
                scratch.insert_pdf(self._doc, from_page=page, to_page=page, widgets=True)
                target = scratch[0]
                for layer in layers:
                    insert_text(target, checked_rect(layer['rect']), layer['text'],
                                layer['fill'], layer['font_family'], layer['font_size'])
            scale = min(1.5, 2000 / max(target.rect.width, target.rect.height),
                        (4000000 / (target.rect.width * target.rect.height)) ** 0.5)
            pixmap = target.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            return pixmap, target.rect.width, target.rect.height
        finally:
            if scratch is not None:
                scratch.close()
