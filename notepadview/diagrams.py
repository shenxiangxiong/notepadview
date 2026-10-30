"""离线只读图形画布，支持分页、缩放和拖动画布浏览。"""
from copy import deepcopy
import math

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt, QTimer, QUrl
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QFont, QImageReader, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QTextOption
from PySide6.QtWidgets import (QGraphicsPathItem, QGraphicsPixmapItem, QGraphicsScene, QGraphicsSimpleTextItem,
    QGraphicsTextItem, QGraphicsView, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget)
import base64

from notepadview.theme import ThemedComboBox as QComboBox

from notepadview.diagram_formats import SUFFIXES, new_diagram, read_diagram, validate, write_diagram


class NodeItem(QGraphicsPathItem):
    def __init__(self, editor, node):
        super().__init__()
        self.editor, self.node_id = editor, node['id']
        self.setAcceptedMouseButtons(Qt.NoButton)
        self.setPos(node['x'], node['y'])
        w, h = node['width'], node['height']
        path = QPainterPath()
        if node['shape'] == 'diamond':
            path.addPolygon(QPolygonF([QPointF(w / 2, 0), QPointF(w, h / 2), QPointF(w / 2, h), QPointF(0, h / 2)]))
            path.closeSubpath()
        elif node['shape'] == 'rounded':
            path.addRoundedRect(0, 0, w, h, 4, 4)
        elif node['shape'] == 'ellipse':
            path.addEllipse(0, 0, w, h)
        elif node['shape'] == 'underline':
            path.moveTo(0, h)
            path.lineTo(w, h)
        else:
            path.addRect(0, 0, w, h)
        self.setPath(path)
        style = node.get('style', {})
        self.setBrush(QBrush(QColor(style.get('fill', '#edf5ff'))))
        pen = QPen(QColor(style.get('stroke', '#376795')), style.get('stroke_width', 2))
        if style.get('stroke_width') == 0:
            pen.setStyle(Qt.NoPen)
        elif style.get('dashed'):
            pen.setStyle(Qt.DashLine)
        self.setPen(pen)
        self.label = QGraphicsTextItem(self)
        self.label.setAcceptedMouseButtons(Qt.NoButton)
        self.label.setTextInteractionFlags(Qt.NoTextInteraction)
        font = QFont(style.get('font_family', 'Microsoft YaHei'))
        font.setPointSizeF(max(1, style.get('font_size', 12)))
        font.setBold(style.get('bold', False))
        font.setItalic(style.get('italic', False))
        font.setUnderline(style.get('underline', False))
        self.label.setFont(font)
        self.label.setDefaultTextColor(QColor(style.get('text_color', '#182c43')))
        option = self.label.document().defaultTextOption()
        option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        option.setAlignment({'left': Qt.AlignLeft, 'right': Qt.AlignRight}.get(style.get('align'), Qt.AlignHCenter))
        self.label.document().setDefaultTextOption(option)
        self.label.document().setDocumentMargin(0)
        self.label.setPlainText(node['text'])
        self.label.setTextWidth(max(10, w - 24))
        self.label.setToolTip(node['text'])
        image_height = 0
        if node.get('image'):
            image = node['image']
            try:
                data = QByteArray(base64.b64decode(image['data'], validate=True))
                buffer = QBuffer(data)
                buffer.open(QIODevice.ReadOnly)
                reader = QImageReader(buffer)
                size = reader.size()
                if size.isValid() and size.width() * size.height() <= 16000000:
                    pixmap = QPixmap.fromImage(reader.read())
                    if not pixmap.isNull():
                        picture = QGraphicsPixmapItem(pixmap, self)
                        picture.setAcceptedMouseButtons(Qt.NoButton)
                        scale = min(image['width'] / pixmap.width(), image['height'] / pixmap.height())
                        picture.setScale(scale)
                        picture.setPos((w - pixmap.width() * scale) / 2, 10)
                        image_height = pixmap.height() * scale + 12
            except ValueError:
                pass
        link_height = 24 if node.get('link') else 0
        self.link_item = None
        if link_height:
            self.link_item = QGraphicsSimpleTextItem('↗ 打开链接', self)
            self.link_item.setFont(QFont('Microsoft YaHei', 9))
            self.link_item.setBrush(QBrush(QColor('#064b83')))
            self.link_item.setPos(10, h - 23)
            self.link_item.setToolTip(node['link'])
            self.link_item.setCursor(Qt.PointingHandCursor)
            self.link_item.setData(0, node['link'])
            self.link_item.setAcceptedMouseButtons(Qt.NoButton)
        available = max(1, h - 8 - image_height - link_height)
        self.label.setScale(min(1, available / max(1, self.label.boundingRect().height())))
        self.label.setPos((w - self.label.boundingRect().width() * self.label.scale()) / 2,
                          image_height + (h - image_height - link_height - self.label.boundingRect().height() * self.label.scale()) / 2)
        self.setZValue(1)

class EdgeItem(QGraphicsPathItem):
    def __init__(self, editor, edge):
        super().__init__()
        self.editor, self.edge = editor, edge
        self.setAcceptedMouseButtons(Qt.NoButton)
        style = edge.get('style', {})
        pen = QPen(QColor(style.get('stroke', '#53718d')), style.get('stroke_width', 2))
        if style.get('dashed'):
            pen.setStyle(Qt.DashLine)
        self.setPen(pen)
        self.label = QGraphicsSimpleTextItem(edge.get('text', ''), self)
        self.label.setBrush(QBrush(QColor('#182c43')))
        self.update_path()

    def update_path(self):
        source = self.editor.items_by_id.get(self.edge['source'])
        target = self.editor.items_by_id.get(self.edge['target'])
        if source is None or target is None:
            return
        def node_rect(item):
            node = self.editor.node(item.node_id)
            return QRectF(0, 0, node['width'], node['height'])
        a = source.mapToScene(node_rect(source).center())
        b = target.mapToScene(node_rect(target).center())
        # 将端点收缩至节点边界，箭头不会藏在节点下方。
        def border(item, center, vx, vy):
            rect = node_rect(item)
            dx, dy = abs(vx) / (rect.width() / 2), abs(vy) / (rect.height() / 2)
            shape = self.editor.node(item.node_id)['shape']
            distance = dx + dy if shape == 'diamond' else math.hypot(dx, dy) if shape == 'ellipse' else max(dx, dy)
            scale = 1 / max(distance, 1e-9)
            return center + QPointF(vx * scale, vy * scale)
        bend = self.edge.get('bend')
        points = [QPointF(p['x'], p['y']) for p in self.edge.get('points', [])]
        if not points and bend:
            points = [QPointF(bend['x'], bend['y'])]
        route = self.edge.get('style', {}).get('route')
        tree = (not points and self.editor.page['kind'] == 'mindmap'
                and self.editor.node(target.node_id).get('parent') == source.node_id)
        down = tree and self.edge.get('style', {}).get('tree_direction') == 'down'
        if tree:
            if down:
                start = border(source, a, 0, 1)
                end = border(target, b, 0, -1)
                middle = (start.y() + end.y()) / 2
                controls = [QPointF(start.x(), middle), QPointF(end.x(), middle)]
            else:
                direction = 1 if b.x() >= a.x() else -1
                start = border(source, a, direction, 0)
                end = border(target, b, -direction, 0)
                middle = (start.x() + end.x()) / 2
                controls = [QPointF(middle, start.y()), QPointF(middle, end.y())]
            if route == 'orthogonal':
                points = controls
        else:
            if not points and route == 'orthogonal':
                points = [QPointF((a.x() + b.x()) / 2, a.y()), QPointF((a.x() + b.x()) / 2, b.y())]
            first, last = (points[0], points[-1]) if points else (b, a)
            start = border(source, a, first.x() - a.x(), first.y() - a.y())
            end = border(target, b, last.x() - b.x(), last.y() - b.y())
        last = points[-1] if points else controls[-1] if tree else a
        path = QPainterPath(start)
        if not points and route == 'curve':
            if tree:
                path.cubicTo(*controls, end)
            else:
                middle_x = (start.x() + end.x()) / 2
                path.cubicTo(QPointF(middle_x, start.y()), QPointF(middle_x, end.y()), end)
        else:
            for point in points:
                path.lineTo(point)
            path.lineTo(end)
        if self.edge.get('arrow', True):
            angle = math.atan2(end.y() - last.y(), end.x() - last.x())
            for delta in (-0.5, 0.5):
                path.moveTo(end)
                path.lineTo(end - QPointF(13 * math.cos(angle + delta), 13 * math.sin(angle + delta)))
        self.setPath(path)
        self.label.setPos((start + end) / 2)

class SummaryItem(QGraphicsPathItem):
    def __init__(self, editor, summary):
        super().__init__()
        from notepadview.diagram_appearance import summary_bounds
        self.setAcceptedMouseButtons(Qt.NoButton)
        style = summary['style']
        self.setPen(QPen(QColor(style.get('stroke', '#10113f')), style.get('stroke_width', 2)))
        x1, y1, x2, y2 = summary_bounds(editor.page, summary)
        target = editor.node(summary['topic'])
        down = target['y'] >= y2 + 20 and x1 <= target['x'] + target['width'] / 2 <= x2
        direction = 1 if down or target['x'] > x2 else -1
        start, finish = (x1 - 4, x2 + 4) if down else (y1 - 4, y2 + 4)
        near = (y2 if down else x2 if direction > 0 else x1) + direction * 12
        far = near + direction * 16
        middle = (start + finish) / 2
        point = (lambda u, v: QPointF(v, u)) if down else (lambda u, v: QPointF(u, v))
        path = QPainterPath(point(near, start))
        path.cubicTo(point(far, start + (finish - start) / 4), point(far, middle), point(far, middle))
        path.cubicTo(point(far, middle), point(far, finish - (finish - start) / 4), point(near, finish))
        path.moveTo(point(far, middle))
        endpoint = (QPointF(target['x'] + target['width'] / 2, target['y']) if down else
                    QPointF(target['x'] if direction > 0 else target['x'] + target['width'],
                            target['y'] + target['height'] / 2))
        path.lineTo(endpoint)
        self.setPath(path)


class CanvasView(QGraphicsView):
    def mousePressEvent(self, event):
        if event.button() in (Qt.LeftButton, Qt.MiddleButton):
            self._pan_position = event.position().toPoint()
            self._press_position = self._pan_position
            self._pan_dragged = False
            item = self.itemAt(self._pan_position)
            self._pressed_link = item.data(0) if item is not None and event.button() == Qt.LeftButton else None
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        previous = getattr(self, '_pan_position', None)
        if previous is not None:
            position = event.position().toPoint()
            if (position - self._press_position).manhattanLength() > 5:
                self._pan_dragged = True
            delta = self.mapToScene(previous) - self.mapToScene(position)
            center = self.mapToScene(self.viewport().rect().center()) + delta
            # 扩展可见范围，避免适应窗口后滚动条范围为零导致平移失效。
            self.center_at(center)
            self._pan_position = position
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() in (Qt.LeftButton, Qt.MiddleButton):
            item = self.itemAt(event.position().toPoint())
            link = getattr(self, '_pressed_link', None)
            if (link and not getattr(self, '_pan_dragged', True)
                    and item is not None and item.data(0) == link):
                QDesktopServices.openUrl(QUrl(link))
            self._pressed_link = None
            self._pan_position = None
            self.setCursor(Qt.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        event.accept()

    def center_at(self, center):
        size = self.viewport().size()
        scale = max(0.001, self.transform().m11())
        area = QRectF(center.x() - size.width() / scale, center.y() - size.height() / scale,
                      2 * size.width() / scale, 2 * size.height() / scale)
        self.setSceneRect(self.sceneRect().united(area))
        self.centerOn(center)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            self.parent().zoom(1.2 if event.angleDelta().y() > 0 else 1 / 1.2)
            event.accept()
        else:
            super().wheelEvent(event)

class DiagramEditor(QWidget):
    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = deepcopy(validate(model))
        self.page_index = 0
        self.items_by_id = {}
        self.edge_items = []
        layout = QVBoxLayout(self)
        tools = QHBoxLayout()
        self.pages = QComboBox()
        self.pages.currentIndexChanged.connect(self.change_page)
        tools.addWidget(self.pages)
        for text, callback in [('−', lambda: self.zoom(0.8)),
                               ('＋', lambda: self.zoom(1.25)), ('适应', self.fit)]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            tools.addWidget(button)
        tools.addStretch()
        layout.addLayout(tools)
        self.notice = QLabel('只读查看；拖动画布浏览；Ctrl+滚轮缩放')
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet('QLabel { color: #e64a19; font-weight: bold; }')
        layout.addWidget(self.notice)
        self.scene = QGraphicsScene(self)
        self.view = CanvasView(self.scene, self)
        self.view.setRenderHint(QPainter.Antialiasing)
        self.view.setCursor(Qt.OpenHandCursor)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setBackgroundBrush(QBrush(QColor('#ffffff')))
        layout.addWidget(self.view, 1)
        self.refresh()

    @property
    def page(self):
        return self.model['pages'][self.page_index]

    def node(self, node_id):
        return next(n for n in self.page['nodes'] if n['id'] == node_id)

    def refresh(self):
        self.page_index = min(self.page_index, len(self.model['pages']) - 1)
        self.pages.blockSignals(True)
        self.pages.clear()
        self.pages.addItems([p['name'] for p in self.model['pages']])
        self.pages.setCurrentIndex(self.page_index)
        self.pages.blockSignals(False)
        self.items_by_id = {}
        self.edge_items = []
        self.summary_items = []
        self.scene.clear()
        self.view.setBackgroundBrush(QBrush(QColor(self.page.get('background', '#ffffff'))))
        for node in self.page['nodes']:
            item = NodeItem(self, node)
            self.items_by_id[node['id']] = item
            self.scene.addItem(item)
        for edge in self.page['edges']:
            item = EdgeItem(self, edge)
            self.edge_items.append(item)
            self.scene.addItem(item)
        for summary in self.page.get('summaries', []):
            item = SummaryItem(self, summary)
            self.summary_items.append(item)
            self.scene.addItem(item)
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-300, -300, 300, 300))
        self.view.setSceneRect(self.scene.sceneRect())
        warnings = self.model.get('warnings', [])
        self.notice.setToolTip('\n'.join(warnings))
        self.notice.setText('只读查看；拖动画布浏览；Ctrl+滚轮缩放')

    def change_page(self, index):
        if 0 <= index < len(self.model['pages']):
            self.page_index = index
            self.refresh()
            self.fit()

    def zoom(self, factor):
        scale = self.view.transform().m11()
        target = max(0.05, min(8, scale * factor))
        self.view.scale(target / scale, target / scale)

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, '_initial_view_done', False):
            self._initial_view_done = True
            QTimer.singleShot(0, self, self._initial_view)

    def _initial_view(self):
        self.fit()
        scale = self.view.transform().m11()
        target = max(0.75, min(1.0, scale))
        self.view.scale(target / scale, target / scale)
        if self.page['nodes']:
            first = self.items_by_id[self.page['nodes'][0]['id']]
            self.view.center_at(first.sceneBoundingRect().center())

    def fit(self):
        self.view.fitInView(self.scene.itemsBoundingRect().adjusted(-40, -40, 40, 40), Qt.KeepAspectRatio)
