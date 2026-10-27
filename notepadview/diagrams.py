"""离线 Qt 图形画布，所有编辑经由可撤销的模型快照。"""
from copy import deepcopy
import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, Signal, Slot, QTimer
from PySide6.QtGui import (QBrush, QColor, QImage, QPainter, QPainterPath, QPen,
                          QPolygonF, QUndoCommand, QUndoStack)
from PySide6.QtSvg import QSvgGenerator
from PySide6.QtWidgets import (QComboBox, QFileDialog, QGraphicsItem, QGraphicsPathItem,
    QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout, QInputDialog,
    QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget)

from notepadview.diagram_formats import (SUFFIXES, identifier, new_diagram, read_diagram,
                                        validate, write_diagram)


class Change(QUndoCommand):
    def __init__(self, editor, before, after, title):
        super().__init__(title)
        self.editor, self.before, self.after = editor, before, after

    def undo(self):
        self.editor.model = deepcopy(self.before)
        self.editor.refresh()

    def redo(self):
        self.editor.model = deepcopy(self.after)
        self.editor.refresh()


class NodeItem(QGraphicsPathItem):
    def __init__(self, editor, node):
        super().__init__()
        self.editor, self.node_id = editor, node['id']
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable |
                      QGraphicsItem.ItemSendsGeometryChanges)
        self.setPos(node['x'], node['y'])
        w, h = node['width'], node['height']
        path = QPainterPath()
        if node['shape'] == 'diamond':
            path.addPolygon(QPolygonF([QPointF(w / 2, 0), QPointF(w, h / 2), QPointF(w / 2, h), QPointF(0, h / 2)]))
            path.closeSubpath()
        elif node['shape'] == 'rounded':
            path.addRoundedRect(0, 0, w, h, 12, 12)
        else:
            path.addRect(0, 0, w, h)
        self.setPath(path)
        self.setBrush(QBrush(QColor('#edf5ff')))
        self.setPen(QPen(QColor('#376795'), 2))
        self.label = QGraphicsSimpleTextItem(self)
        self.label.setBrush(QBrush(QColor('#182c43')))
        self.label.setText(node['text'])
        self.label.setToolTip(node['text'])
        self.label.setScale(min(1, (w - 20) / max(1, self.label.boundingRect().width()),
                                (h - 16) / max(1, self.label.boundingRect().height())))
        self.label.setPos((w - self.label.boundingRect().width() * self.label.scale()) / 2,
                          (h - self.label.boundingRect().height() * self.label.scale()) / 2)
        self.setZValue(1)

    def mousePressEvent(self, event):
        if self.editor.connecting:
            self.editor.connect_click(self.node_id)
            event.accept()
            return
        self.editor.drag_before = deepcopy(self.editor.model)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self.editor.drag_before is not None:
            before = self.editor.drag_before
            self.editor.drag_before = None
            for item in self.scene().selectedItems():
                if isinstance(item, NodeItem):
                    node = self.editor.node(item.node_id)
                    node['x'], node['y'] = item.pos().x(), item.pos().y()
            self.editor.commit(before, '移动节点')

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged and self.scene() is not None:
            self.editor.redraw_edges()
        return super().itemChange(change, value)

    def mouseDoubleClickEvent(self, event):
        text, ok = QInputDialog.getMultiLineText(self.editor, '编辑节点', '文本', self.editor.node(self.node_id)['text'])
        if ok:
            self.editor.set_node_text(self.node_id, text)
        event.accept()


class EdgeItem(QGraphicsPathItem):
    def __init__(self, editor, edge):
        super().__init__()
        self.editor, self.edge = editor, edge
        self.setFlag(QGraphicsItem.ItemIsSelectable)
        self.setPen(QPen(QColor('#53718d'), 2))
        self.setFlag(QGraphicsItem.ItemIsMovable)
        self.label = QGraphicsSimpleTextItem(edge.get('text', ''), self)
        self.label.setBrush(QBrush(QColor('#182c43')))
        self.update_path()

    def mousePressEvent(self, event):
        self.drag_start = event.scenePos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        delta = event.scenePos() - self.drag_start
        if delta.manhattanLength() > 2:
            before = deepcopy(self.editor.model)
            source = self.editor.items_by_id[self.edge['source']].sceneBoundingRect().center()
            target = self.editor.items_by_id[self.edge['target']].sceneBoundingRect().center()
            midpoint = (source + target) / 2
            bend = self.edge.get('bend', {'x': midpoint.x(), 'y': midpoint.y()})
            self.edge['bend'] = {'x': bend['x'] + delta.x(), 'y': bend['y'] + delta.y()}
            self.editor.commit(before, '拖动连线')
        else:
            self.setPos(0, 0)

    def update_path(self):
        source = self.editor.items_by_id.get(self.edge['source'])
        target = self.editor.items_by_id.get(self.edge['target'])
        if source is None or target is None:
            return
        a = source.mapToScene(source.boundingRect().center())
        b = target.mapToScene(target.boundingRect().center())
        dx, dy = b.x() - a.x(), b.y() - a.y()
        # 将端点收缩至节点边界，箭头不会藏在节点下方。
        def border(item, center, vx, vy):
            rect = item.path().boundingRect()
            scale = 1 / max(abs(vx) / (rect.width() / 2), abs(vy) / (rect.height() / 2), 1e-9)
            if self.editor.node(item.node_id)['shape'] == 'diamond':
                scale = 1 / max(abs(vx) / (rect.width() / 2) + abs(vy) / (rect.height() / 2), 1e-9)
            return center + QPointF(vx * scale, vy * scale)
        bend = self.edge.get('bend')
        middle = QPointF(bend['x'], bend['y']) if bend else (a + b) / 2
        start = border(source, a, middle.x() - a.x(), middle.y() - a.y())
        end = border(target, b, middle.x() - b.x(), middle.y() - b.y())
        path = QPainterPath(start)
        if bend:
            path.lineTo(middle)
        path.lineTo(end)
        if self.edge.get('arrow', True):
            angle = math.atan2(end.y() - middle.y(), end.x() - middle.x())
            for delta in (-0.5, 0.5):
                path.moveTo(end)
                path.lineTo(end - QPointF(13 * math.cos(angle + delta), 13 * math.sin(angle + delta)))
        self.setPath(path)
        self.label.setPos((start + end) / 2)

    def mouseDoubleClickEvent(self, event):
        text, ok = QInputDialog.getMultiLineText(self.editor, '编辑连线', '文本', self.edge.get('text', ''))
        if ok:
            before = deepcopy(self.editor.model)
            self.edge['text'] = text
            self.editor.commit(before, '编辑连线')
        event.accept()


class CanvasView(QGraphicsView):
    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            self.parent().zoom(1.2 if event.angleDelta().y() > 0 else 1 / 1.2)
            event.accept()
        else:
            super().wheelEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.parent().delete_selected()
        else:
            super().keyPressEvent(event)


class DiagramEditor(QWidget):
    changed = Signal()

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = deepcopy(validate(model))
        self.page_index = 0
        self.undo_stack = QUndoStack(self)
        self.undo_stack.indexChanged.connect(self._stack_changed)
        self.undo_stack.cleanChanged.connect(self._clean_changed)
        self.items_by_id = {}
        self.edge_items = []
        self.connecting = False
        self.connection_source = None
        self.drag_before = None
        layout = QVBoxLayout(self)
        tools = QHBoxLayout()
        self.pages = QComboBox()
        self.pages.currentIndexChanged.connect(self.change_page)
        tools.addWidget(self.pages)
        self.shape = QComboBox()
        self.shape.addItems(['矩形', '圆角', '菱形'])
        tools.addWidget(self.shape)
        for text, callback in [('添加节点', lambda: self.add_node()), ('添加子主题', self.add_child),
                               ('连线', self.start_connect), ('删除', self.delete_selected),
                               ('自动布局', self.auto_layout)]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            tools.addWidget(button)
        layout.addLayout(tools)
        extra = QHBoxLayout()
        for text, callback in [('新增流程页', lambda: self.add_page('flowchart')),
                               ('新增脑图页', lambda: self.add_page('mindmap')),
                               ('重命名页', self.rename_page), ('撤销', self.undo_stack.undo),
                               ('重做', self.undo_stack.redo), ('−', lambda: self.zoom(0.8)),
                               ('＋', lambda: self.zoom(1.25)), ('适应', self.fit), ('导出 SVG/PNG', self.choose_export)]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            extra.addWidget(button)
        layout.addLayout(extra)
        self.notice = QLabel('双击编辑文本；拖动节点；连线后依次点击起点/终点；Ctrl+滚轮缩放；Delete 删除选中项。')
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.scene = QGraphicsScene(self)
        self.view = CanvasView(self.scene, self)
        self.view.setRenderHint(QPainter.Antialiasing)
        self.view.setDragMode(QGraphicsView.RubberBandDrag)
        self.view.setBackgroundBrush(QBrush(QColor('#ffffff')))
        layout.addWidget(self.view, 1)
        self.refresh()

    @Slot(int)
    def _stack_changed(self, _index):
        self.changed.emit()

    @Slot(bool)
    def _clean_changed(self, _clean):
        self.changed.emit()

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
        self.scene.clear()
        for node in self.page['nodes']:
            item = NodeItem(self, node)
            self.items_by_id[node['id']] = item
            self.scene.addItem(item)
        for edge in self.page['edges']:
            item = EdgeItem(self, edge)
            self.edge_items.append(item)
            self.scene.addItem(item)
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-300, -300, 300, 300))
        warnings = self.model.get('warnings', [])
        self.notice.setToolTip('\n'.join(warnings))
        if warnings:
            self.notice.setText('⚠ 原文件保护：' + '；'.join(warnings) + '。保存时须另存；双击编辑，拖动节点，Delete 删除。')

    def redraw_edges(self):
        for item in self.edge_items:
            item.update_path()

    def change_page(self, index):
        if 0 <= index < len(self.model['pages']):
            self.page_index = index
            self.refresh()
            self.fit()

    def commit(self, before, title):
        if before != self.model:
            after = deepcopy(self.model)
            try:
                validate(after)
            except ValueError as error:
                self.model = before
                self.refresh()
                QMessageBox.warning(self, '无法编辑', str(error))
                return
            self.undo_stack.push(Change(self, before, after, title))

    def add_node(self, parent_id=None):
        before = deepcopy(self.model)
        node_id = identifier()
        parent = self.node(parent_id) if parent_id else None
        self.page['nodes'].append({'id': node_id, 'text': '子主题' if parent else '新节点',
            'x': parent['x'] + 230 if parent else len(self.page['nodes']) * 40,
            'y': parent['y'] + 100 if parent else len(self.page['nodes']) * 90,
            'width': 150.0, 'height': 64.0, 'parent': parent_id,
            'shape': ['rectangle', 'rounded', 'diamond'][self.shape.currentIndex()]})
        if parent:
            self.page['edges'].append({'id': identifier(), 'source': parent_id, 'target': node_id, 'text': '', 'arrow': True})
        self.commit(before, '添加节点')
        return node_id

    def add_child(self):
        selected = [i.node_id for i in self.scene.selectedItems() if isinstance(i, NodeItem)]
        if not selected and self.page['nodes']:
            selected = [self.page['nodes'][0]['id']]
        if selected:
            self.add_node(selected[0])

    def set_node_text(self, node_id, text):
        before = deepcopy(self.model)
        self.node(node_id)['text'] = text
        self.commit(before, '编辑文本')

    def move_node(self, node_id, x, y):
        before = deepcopy(self.model)
        self.node(node_id).update(x=x, y=y)
        self.commit(before, '移动节点')

    def start_connect(self):
        self.connecting = not self.connecting
        self.connection_source = None
        self.view.setCursor(Qt.CrossCursor if self.connecting else Qt.ArrowCursor)
        self.notice.setText('依次点击两个节点建立箭头连接；再次点击“连线”取消。' if self.connecting else '双击编辑文本；拖动节点。')

    def connect_click(self, node_id):
        if self.connection_source is None:
            self.connection_source = node_id
        elif node_id != self.connection_source:
            before = deepcopy(self.model)
            self.page['edges'].append({'id': identifier(), 'source': self.connection_source,
                                      'target': node_id, 'text': '', 'arrow': True})
            self.start_connect()
            self.commit(before, '添加连线')

    def delete_selected(self):
        self.delete_ids({i.node_id if isinstance(i, NodeItem) else i.edge['id']
                         for i in self.scene.selectedItems() if isinstance(i, (NodeItem, EdgeItem))})

    def delete_ids(self, ids):
        before = deepcopy(self.model)
        self.page['nodes'] = [n for n in self.page['nodes'] if n['id'] not in ids]
        for node in self.page['nodes']:
            if node.get('parent') in ids:
                node['parent'] = None
        self.page['edges'] = [e for e in self.page['edges'] if not {e['id'], e['source'], e['target']} & ids]
        self.commit(before, '删除节点或连线')

    def auto_layout(self):
        before = deepcopy(self.model)
        nodes = self.page['nodes']
        if self.page['kind'] == 'mindmap':
            row = [0]
            def place(node, depth):
                children = [n for n in nodes if n.get('parent') == node['id']]
                ys = [place(child, depth + 1) for child in children]
                y = sum(ys) / len(ys) if ys else row[0] * 110
                if not ys:
                    row[0] += 1
                node.update(x=depth * 250, y=y)
                return y
            for root in [n for n in nodes if not n.get('parent')]:
                place(root, 0)
        else:
            for index, node in enumerate(nodes):
                node.update(x=(index % 4) * 250, y=(index // 4) * 160)
        self.commit(before, '自动布局')
        self.fit()

    def add_page(self, kind):
        before = deepcopy(self.model)
        page = new_diagram(kind)['pages'][0]
        page['name'] = '第 ' + str(len(self.model['pages']) + 1) + ' 页'
        self.model['pages'].append(page)
        self.page_index = len(self.model['pages']) - 1
        self.commit(before, '新增页面')

    def rename_page(self):
        text, ok = QInputDialog.getText(self, '重命名页面', '页面名称', text=self.page['name'])
        if ok and text.strip():
            before = deepcopy(self.model)
            self.page['name'] = text.strip()
            self.commit(before, '重命名页面')

    def zoom(self, factor):
        scale = self.view.transform().m11()
        target = max(0.05, min(8, scale * factor))
        self.view.scale(target / scale, target / scale)

    def showEvent(self, event):
        super().showEvent(event)
        self.fit()
        QTimer.singleShot(0, self, self.fit)

    def fit(self):
        self.view.fitInView(self.scene.itemsBoundingRect().adjusted(-40, -40, 40, 40), Qt.KeepAspectRatio)

    def choose_export(self):
        path, selected = QFileDialog.getSaveFileName(self, '导出当前页', '', 'SVG (*.svg);;PNG (*.png)')
        if path:
            if not Path(path).suffix:
                path += '.png' if selected.startswith('PNG') else '.svg'
            try:
                self.export_image(path)
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, '导出失败', str(error))

    def export_image(self, path):
        path = Path(path)
        if path.suffix.lower() not in ('.svg', '.png'):
            raise ValueError('导出请选择 SVG 或 PNG。')
        if any(path.resolve() == Path(p).resolve() for p in self.model.get('protected_paths', [])):
            raise ValueError('禁止导出覆盖原文件。')
        rect = self.scene.itemsBoundingRect().adjusted(-24, -24, 24, 24)
        width, height = max(1, math.ceil(rect.width())), max(1, math.ceil(rect.height()))
        if width * height > 40000000 or max(width, height) > 16000:
            raise ValueError('当前画布过大，请缩小节点间距后导出。')
        selected = self.scene.selectedItems()
        self.scene.clearSelection()
        try:
            if path.suffix.lower() == '.svg':
                target = QSvgGenerator()
                target.setFileName(str(path))
                from PySide6.QtCore import QSize
                target.setSize(QSize(width, height))
                target.setViewBox(QRectF(0, 0, width, height))
            else:
                target = QImage(width, height, QImage.Format_ARGB32)
                target.fill(Qt.white)
            painter = QPainter(target)
            if not painter.isActive():
                raise OSError('无法创建导出图像。')
            self.scene.render(painter, QRectF(0, 0, width, height), rect)
            painter.end()
            if path.suffix.lower() == '.png' and not target.save(str(path), 'PNG'):
                raise OSError('PNG 保存失败。')
            if not path.exists() or not path.stat().st_size:
                raise OSError('图像导出失败。')
        finally:
            for item in selected:
                item.setSelected(True)
