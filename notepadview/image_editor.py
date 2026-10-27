"""图片编辑、独立撤销历史及可恢复的工作区快照。"""
import base64
from copy import deepcopy
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from xml.parsers import expat

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF, QSaveFile, Qt, Signal, Slot, QTimer
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QImage, QImageReader, QPainter, QPen,
                          QTextCharFormat, QTextCursor, QTransform, QUndoCommand, QUndoStack)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QDoubleSpinBox, QFontComboBox,
    QGraphicsItem, QGraphicsScene,
    QGraphicsTextItem, QGraphicsView, QHBoxLayout, QInputDialog, QLabel,
    QMessageBox, QPushButton, QSpinBox, QStyledItemDelegate, QVBoxLayout, QWidget)


def safe_svg(data):
    def reject(*args):
        raise ValueError('SVG 不支持 DTD、实体或外部处理指令。')
    parser = expat.ParserCreate()
    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.ExternalEntityRefHandler = reject
    parser.ProcessingInstructionHandler = reject
    try:
        parser.Parse(data, True)
        root = ET.fromstring(data)
    except (expat.ExpatError, ET.ParseError) as error:
        raise ValueError('SVG XML 格式无效。') from error
    if root.tag.split('}')[-1] != 'svg':
        raise ValueError('不是 SVG 文档。')
    for element in root.iter():
        if element.tag.split('}')[-1] in ('script', 'foreignObject'):
            raise ValueError('SVG 不支持脚本或嵌入网页。')
        for key, value in element.attrib.items():
            if key.split('}')[-1].lower().startswith('on'):
                raise ValueError('SVG 不支持事件脚本。')
            if key.split('}')[-1] == 'href' and not value.startswith('#'):
                raise ValueError('SVG 不支持外部或嵌入资源引用。')
        content = ' '.join(element.attrib.values()) + (element.text or '')
        urls = re.findall(r'url\(([^)]*)\)', content, re.I)
        if re.search(r'@import', content, re.I) or any(
                not url.strip().strip('\"\'').startswith('#') for url in urls):
            raise ValueError('SVG 不支持外部样式资源。')
    used = {node.get('id') for node in root.iter() if node.get('id')}
    for index, node in enumerate(root.iter()):
        if not node.get('id'):
            identifier = f'nv_element_{index}'
            while identifier in used:
                identifier += '_'
            node.set('id', identifier)
            used.add(identifier)
    return root


def image_bytes(image):
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    if not image.save(buffer, 'PNG'):
        raise ValueError('无法缓存图片。')
    return base64.b64encode(bytes(buffer.data())).decode('ascii')


class ImageChange(QUndoCommand):
    def __init__(self, editor, before, after, label):
        super().__init__(label)
        self.editor, self.before, self.after = editor, before, after

    def undo(self):
        self.editor._restore(self.before)

    def redo(self):
        self.editor._restore(self.after)


class TextItem(QGraphicsTextItem):
    def __init__(self, editor, index, layer):
        super().__init__(layer['text'])
        self.editor, self.index = editor, index
        font = QFont(layer['font_family'])
        font.setPixelSize(max(1, round(layer['font_size'])))
        self.setFont(font)
        self.setDefaultTextColor(QColor(layer['fill']))
        cursor = QTextCursor(self.document())
        cursor.select(QTextCursor.Document)
        char_format = QTextCharFormat()
        char_format.setForeground(QColor(layer['fill']))
        cursor.mergeCharFormat(char_format)
        self.setPos(layer['x'], layer['y'])
        self.setTransform(QTransform.fromScale(-1 if layer.get('flip_x') else 1,
                                                -1 if layer.get('flip_y') else 1))
        self.setTextWidth(-1)
        self.document().setDocumentMargin(0)
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable)

    def mousePressEvent(self, event):
        self.editor.selected = self.index
        self.editor._sync_controls()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        position = self.pos()
        editor, index = self.editor, self.index
        def commit():
            editor.selected = index
            editor.move_text(position.x(), position.y())
        QTimer.singleShot(0, editor, commit)


class ImageView(QGraphicsView):
    def mousePressEvent(self, event):
        if self.parent().svg_root is not None:
            self.parent().select_at(self.mapToScene(event.position().toPoint()))
        super().mousePressEvent(event)


class FontChoiceDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.font.setPixelSize(12)
        option.fontMetrics = QFontMetrics(option.font)


class ImageEditor(QWidget):
    changed = Signal()

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.undo_stack = QUndoStack(self)
        self.selected = None
        self.svg_root = None
        self.layers = []
        self._state = None
        self._fitted = True
        self.setObjectName('imageEditor')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)
        tools_panel = QWidget(self)
        tools_panel.setObjectName('imageToolsPanel')
        tools = QHBoxLayout(tools_panel)
        tools.setContentsMargins(12, 10, 12, 10)
        tools.setSpacing(8)
        self.width_box, self.height_box = QSpinBox(), QSpinBox()
        for box in (self.width_box, self.height_box):
            box.setRange(1, 32768)
            box.setSuffix(' px')
            box.setMinimumWidth(136)
        tools.addWidget(QLabel('画布尺寸'))
        self.keep_ratio = QCheckBox('等比例')
        self.keep_ratio.setChecked(True)
        for label, widget in [('宽', self.width_box), ('高', self.height_box)]:
            tools.addWidget(QLabel(label))
            tools.addWidget(widget)
        tools.addWidget(self.keep_ratio)
        self.width_box.valueChanged.connect(lambda value: self._dimension_changed(value, True))
        self.height_box.valueChanged.connect(lambda value: self._dimension_changed(value, False))
        for box in (self.width_box, self.height_box):
            box.setKeyboardTracking(False)
            box.editingFinished.connect(self._apply_dimensions)
            box.setFixedWidth(136)
        layout.addWidget(tools_panel)
        styles = tools
        self.add_button = QPushButton('＋ 添加文字')
        self.add_button.clicked.connect(self._ask_text)
        styles.addStretch()
        styles.addWidget(self.add_button)
        for label, key in [('填充 / 文字颜色', 'fill'), ('描边颜色', 'stroke')]:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, k=key: self._choose_color(k))
            styles.addWidget(button)
            if key == 'stroke':
                self.stroke_button = button
        self.font_box = QFontComboBox()
        self.font_box.setItemDelegate(FontChoiceDelegate(self.font_box))
        self.font_size = QDoubleSpinBox()
        self.font_size.setRange(1, 2000)
        self.font_size.setValue(24)
        self.font_box.currentFontChanged.connect(lambda font: self.set_style(font_family=font.family()))
        self.font_size.valueChanged.connect(lambda value: self.set_style(font_size=value))
        styles.addSpacing(8)
        styles.addWidget(QLabel('字体'))
        self.font_box.setFixedWidth(160)
        styles.addWidget(self.font_box)
        styles.addWidget(QLabel('字号'))
        self.font_size.setSuffix(' px')
        self.font_size.setFixedWidth(152)
        styles.addWidget(self.font_size)
        for label, callback in [('水平翻转', lambda: self.flip_image(True)),
                                ('垂直翻转', lambda: self.flip_image(False)),
                                ('缩小', lambda: self.zoom(0.8)), ('放大', lambda: self.zoom(1.25)), ('适应窗口', self.fit)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            tools.addWidget(button)
        self.hint = QLabel('点击选择图形或文字 · 拖动调整文字位置 · Ctrl+S 保存')
        self.hint.setObjectName('imageEditorHint')
        layout.addWidget(self.hint)
        self.scene = QGraphicsScene(self)
        self.view = ImageView(self.scene, self)
        self.view.setObjectName('imageCanvas')
        self.view.setRenderHint(QPainter.Antialiasing)
        layout.addWidget(self.view, 1)
        self._restore(state)
        self.undo_stack.indexChanged.connect(self._history_changed)
        self.undo_stack.cleanChanged.connect(self._history_changed)

    @Slot()
    def _history_changed(self):
        self.changed.emit()

    @classmethod
    def from_path(cls, path, parent=None):
        path = Path(path)
        if path.suffix.lower() == '.svg':
            root = safe_svg(path.read_bytes())
            data = ET.tostring(root, encoding='utf-8')
            renderer = QSvgRenderer(QByteArray(data))
            if not renderer.isValid() or renderer.defaultSize().isEmpty():
                raise ValueError('SVG 无法渲染或尺寸无效。')
            size = renderer.defaultSize()
            state = {'format': 'svg', 'svg': data.decode('utf-8'), 'width': size.width(), 'height': size.height(), 'layers': []}
        else:
            reader = QImageReader(str(path))
            reader.setDecideFormatFromContent(True)
            reader.setAutoTransform(True)
            fmt = bytes(reader.format()).decode('ascii').lower()
            image = reader.read()
            if fmt not in ('png', 'jpg', 'jpeg') or image.isNull():
                raise ValueError('可编辑位图仅支持 PNG / JPEG。')
            state = {'format': 'jpeg' if fmt in ('jpg', 'jpeg') else 'png', 'raster': image_bytes(image),
                     'width': image.width(), 'height': image.height(), 'layers': []}
        return cls(state, parent)

    @classmethod
    def from_snapshot(cls, snapshot, parent=None):
        editor = cls(snapshot['initial'], parent)
        for change in snapshot.get('history', []):
            editor.undo_stack.push(ImageChange(editor, change['before'], change['after'], change['label']))
        clean = snapshot.get('clean', 0)
        if clean >= 0:
            editor.undo_stack.setIndex(clean)
            editor.undo_stack.setClean()
        else:
            editor.undo_stack.resetClean()
        editor.undo_stack.setIndex(snapshot.get('index', 0))
        return editor

    def snapshot(self):
        history = []
        for index in range(self.undo_stack.count()):
            command = self.undo_stack.command(index)
            history.append({'before': command.before, 'after': command.after, 'label': command.text()})
        return deepcopy({'initial': history[0]['before'] if history else self._state,
                         'history': history, 'index': self.undo_stack.index(), 'clean': self.undo_stack.cleanIndex()})

    @property
    def image_size(self):
        return self._state['width'], self._state['height']

    @property
    def image_format(self):
        return self._state['format']

    def _restore(self, state):
        self._state = deepcopy(state)
        self.svg_root = safe_svg(state['svg'].encode('utf-8')) if state['format'] == 'svg' else None
        if self.svg_root is not None:
            group = next((node for node in self.svg_root if node.get('data-nv-flip') == 'canvas'), None)
            if group is not None:
                for key in ('flip_x', 'flip_y'):
                    self._state.setdefault(key, group.get('data-nv-' + key) == 'true')
        self.layers = self._state['layers']
        self._refresh()

    def _commit(self, state, label):
        if state != self._state:
            self.undo_stack.push(ImageChange(self, deepcopy(self._state), state, label))

    def _renderer(self):
        renderer = QSvgRenderer(QByteArray(ET.tostring(self.svg_root, encoding='utf-8')))
        if not renderer.isValid():
            raise ValueError('SVG 无法渲染。')
        return renderer

    def _bounds(self, renderer, identifier):
        rect = renderer.transformForElement(identifier).mapRect(renderer.boundsOnElement(identifier))
        view = renderer.viewBoxF()
        width, height = self.image_size
        # QSvgRenderer 默认把 viewBox 拉伸到目标矩形；命中框采用相同映射。
        if view.width() and view.height():
            sx, sy = width / view.width(), height / view.height()
            return QRectF((rect.x() - view.x()) * sx, (rect.y() - view.y()) * sy,
                          rect.width() * sx, rect.height() * sy)
        return rect

    def select_element(self, identifier):
        self.selected = identifier
        self._refresh()

    def select_at(self, point):
        renderer = self._renderer()
        self.selected = None
        for element in reversed(list(self.svg_root.iter())):
            if element.tag.split('}')[-1] not in ('rect', 'circle', 'ellipse', 'path', 'polygon', 'polyline', 'line', 'text', 'use'):
                continue
            identifier = element.get('id')
            if renderer.elementExists(identifier) and self._bounds(renderer, identifier).adjusted(-2, -2, 2, 2).contains(point):
                self.selected = identifier
                break
        self._refresh()
        return self.selected is not None

    def set_style(self, **properties):
        if self.selected is None:
            return
        state = deepcopy(self._state)
        if self.svg_root is not None:
            root = safe_svg(state['svg'].encode('utf-8'))
            node = next((n for n in root.iter() if n.get('id') == self.selected), None)
            if node is None:
                return
            text = node.tag.split('}')[-1] == 'text'
            style = dict(part.split(':', 1) for part in node.get('style', '').split(';') if ':' in part)
            for key, value in properties.items():
                if key.startswith('font_') and not text:
                    continue
                name = key.replace('_', '-')
                style[name] = str(value)
                node.set(name, str(value))
            node.set('style', ';'.join(f'{key.strip()}:{value.strip()}' for key, value in style.items()))
            state['svg'] = ET.tostring(root, encoding='unicode')
        elif isinstance(self.selected, int) and self.selected < len(self.layers):
            state['layers'][self.selected].update({key: value for key, value in properties.items() if key != 'stroke'})
        self._commit(state, '修改样式')

    def add_text(self, text, x=20, y=20):
        if self.svg_root is not None or not text:
            return
        state = deepcopy(self._state)
        state['layers'].append({'text': text, 'x': x, 'y': y, 'fill': '#000000',
                                'font_family': self.font_box.currentFont().family(), 'font_size': self.font_size.value()})
        self.selected = len(state['layers']) - 1
        self._commit(state, '添加文字')

    def move_text(self, x, y):
        if self.svg_root is not None or not isinstance(self.selected, int):
            return
        state = deepcopy(self._state)
        state['layers'][self.selected].update(x=x, y=y)
        self._commit(state, '移动文字')

    def resize_image(self, width, height):
        if not (1 <= width <= 32768 and 1 <= height <= 32768) or width * height > 100_000_000:
            raise ValueError('尺寸过大：每边最多 32768 像素，总计最多一亿像素。')
        state = deepcopy(self._state)
        old_width, old_height = self.image_size
        state.update(width=int(width), height=int(height))
        if self.svg_root is not None:
            root = safe_svg(state['svg'].encode('utf-8'))
            if not root.get('viewBox'):
                root.set('viewBox', f'0 0 {old_width} {old_height}')
            root.set('width', str(width))
            root.set('height', str(height))
            state['svg'] = ET.tostring(root, encoding='unicode')
        else:
            for layer in state['layers']:
                layer['x'] *= width / old_width
                layer['y'] *= height / old_height
                layer['font_size'] *= min(width / old_width, height / old_height)
        self._commit(state, '调整图片尺寸')

    def flip_image(self, horizontal=True):
        state = deepcopy(self._state)
        key = 'flip_x' if horizontal else 'flip_y'
        state[key] = not state.get(key, False)
        width, height = self.image_size
        if self.svg_root is not None:
            root = safe_svg(state['svg'].encode('utf-8'))
            group = next((node for node in root if node.get('data-nv-flip') == 'canvas'), None)
            if group is None:
                namespace = root.tag[:-3]
                group = ET.Element(namespace + 'g', {'data-nv-flip': 'canvas'})
                for node in list(root):
                    if node.tag.split('}')[-1] not in ('defs', 'style', 'title', 'desc', 'metadata'):
                        root.remove(node)
                        group.append(node)
                root.append(group)
            view = self._renderer().viewBoxF()
            sx = -1 if state.get('flip_x') else 1
            sy = -1 if state.get('flip_y') else 1
            tx = 2 * view.x() + view.width() if sx < 0 else 0
            ty = 2 * view.y() + view.height() if sy < 0 else 0
            group.set('transform', f'matrix({sx} 0 0 {sy} {tx:g} {ty:g})')
            for axis in ('flip_x', 'flip_y'):
                group.set('data-nv-' + axis, str(state.get(axis, False)).lower())
            state['svg'] = ET.tostring(root, encoding='unicode')
        else:
            for layer in state['layers']:
                coordinate, extent = ('x', width) if horizontal else ('y', height)
                layer[coordinate] = extent - layer[coordinate]
                layer[key] = not layer.get(key, False)
        self._commit(state, '水平翻转' if horizontal else '垂直翻转')

    def _raster(self):
        image = QImage.fromData(QByteArray.fromBase64(self._state['raster'].encode('ascii')), 'PNG')
        if image.isNull():
            raise ValueError('图片缓存损坏。')
        orientation = ((Qt.Horizontal if self._state.get('flip_x') else Qt.Orientation(0)) |
                       (Qt.Vertical if self._state.get('flip_y') else Qt.Orientation(0)))
        return image.scaled(*self.image_size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).flipped(orientation)

    def _refresh(self):
        self.scene.clear()
        width, height = self.image_size
        if self.svg_root is not None:
            image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
            image.fill(Qt.transparent)
            painter = QPainter(image)
            renderer = self._renderer()
            renderer.render(painter, QRectF(0, 0, width, height))
            painter.end()
            from PySide6.QtGui import QPixmap
            self.scene.addPixmap(QPixmap.fromImage(image))
            if self.selected and renderer.elementExists(self.selected):
                item = self.scene.addRect(self._bounds(renderer, self.selected), QPen(QColor('#3478f6'), 1, Qt.DashLine))
                item.setZValue(2)
        else:
            from PySide6.QtGui import QPixmap
            self.scene.addPixmap(QPixmap.fromImage(self._raster()))
            for index, layer in enumerate(self.layers):
                item = TextItem(self, index, layer)
                self.scene.addItem(item)
                item.setSelected(index == self.selected)
        self.scene.setSceneRect(0, 0, width, height)
        for box, value in ((self.width_box, width), (self.height_box, height)):
            box.blockSignals(True)
            box.setValue(value)
            box.blockSignals(False)
        self.add_button.setVisible(self.svg_root is None)
        self.stroke_button.setVisible(self.svg_root is not None)
        self._sync_controls()
        if self._fitted:
            self.fit()

    def _sync_controls(self):
        text = False
        values = {}
        if self.svg_root is not None:
            node = next((n for n in self.svg_root.iter() if n.get('id') == self.selected), None)
            text = node is not None and node.tag.split('}')[-1] == 'text'
            if text:
                values = {key.replace('-', '_'): value for key, value in node.attrib.items()}
                values.update({key.strip().replace('-', '_'): value.strip() for key, value in
                               (part.split(':', 1) for part in node.get('style', '').split(';') if ':' in part)})
        elif isinstance(self.selected, int) and self.selected < len(self.layers):
            text, values = True, self.layers[self.selected]
        self.font_box.setEnabled(text or self.svg_root is None)
        self.font_size.setEnabled(text or self.svg_root is None)
        self.font_box.blockSignals(True)
        self.font_size.blockSignals(True)
        if 'font_family' in values:
            self.font_box.setCurrentFont(QFont(values['font_family']))
        if 'font_size' in values:
            try:
                self.font_size.setValue(float(str(values['font_size']).removesuffix('px')))
            except ValueError:
                pass
        self.font_box.blockSignals(False)
        self.font_size.blockSignals(False)

    def _apply_dimensions(self):
        if (self.width_box.value(), self.height_box.value()) != self.image_size:
            self._guard(lambda: self.resize_image(self.width_box.value(), self.height_box.value()))

    def _dimension_changed(self, value, is_width):
        if self.keep_ratio.isChecked():
            width, height = self.image_size
            other = self.height_box if is_width else self.width_box
            other.blockSignals(True)
            other.setValue(max(1, round(value * (height / width if is_width else width / height))))
            other.blockSignals(False)

    def _guard(self, callback):
        try:
            callback()
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, '图片编辑失败', str(error))

    def _ask_text(self):
        text, ok = QInputDialog.getText(self, '添加文字', '文字内容：')
        if ok:
            self.add_text(text)

    def _choose_color(self, key):
        if self.selected is None:
            self.hint.setText('请先点击选择图形或文字。')
            return
        color = QColorDialog.getColor(parent=self, title='选择颜色')
        if color.isValid():
            self.set_style(**{key: color.name()})

    def fit(self):
        self._fitted = True
        self.view.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)

    def zoom(self, factor):
        self._fitted = False
        self.view.scale(factor, factor)

    def showEvent(self, event):
        super().showEvent(event)
        self.fit()
        QTimer.singleShot(0, self, self.fit)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fitted:
            self.fit()

    def save(self, path):
        suffix = Path(path).suffix.lower()
        if self.svg_root is not None:
            if suffix != '.svg':
                raise ValueError('SVG 请保存为 .svg，以保留矢量元素。')
            data = ET.tostring(self.svg_root, encoding='utf-8', xml_declaration=True)
        else:
            fmt = {'.png': 'PNG', '.jpg': 'JPEG', '.jpeg': 'JPEG'}.get(suffix)
            if not fmt:
                raise ValueError('位图请保存为 .png、.jpg 或 .jpeg。')
            image = QImage(*self.image_size, QImage.Format_ARGB32_Premultiplied)
            image.fill(Qt.white if fmt == 'JPEG' else Qt.transparent)
            painter = QPainter(image)
            painter.drawImage(0, 0, self._raster())
            for layer in self.layers:
                item = TextItem(self, 0, layer)
                flip_x, flip_y = layer.get('flip_x', False), layer.get('flip_y', False)
                # 先按原方向栅格化再镜像，避免字体 hinting 改变导出的字形像素。
                text_image = QImage(*self.image_size, QImage.Format_ARGB32_Premultiplied)
                text_image.fill(Qt.transparent)
                text_painter = QPainter(text_image)
                text_painter.translate(self.image_size[0] - layer['x'] if flip_x else layer['x'],
                                       self.image_size[1] - layer['y'] if flip_y else layer['y'])
                item.document().drawContents(text_painter)
                text_painter.end()
                orientation = ((Qt.Horizontal if flip_x else Qt.Orientation(0)) |
                               (Qt.Vertical if flip_y else Qt.Orientation(0)))
                painter.drawImage(0, 0, text_image.flipped(orientation))
            painter.end()
            buffer = QBuffer()
            buffer.open(QIODevice.WriteOnly)
            if not image.save(buffer, fmt):
                raise ValueError('Qt 无法编码目标图片格式。')
            data = bytes(buffer.data())
        output = QSaveFile(str(path))
        if not output.open(QIODevice.WriteOnly):
            raise OSError(output.errorString())
        if output.write(data) != len(data):
            output.cancelWriting()
            raise OSError(output.errorString())
        if not output.commit():
            raise OSError(output.errorString())
