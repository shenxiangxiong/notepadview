"""PDF 单页画布、编辑工具与独立可恢复撤销历史。"""
from copy import deepcopy
from pathlib import Path

import pymupdf as fitz
from PySide6.QtCore import QRectF, QSaveFile, QIODevice, Qt, Signal, Slot, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPixmap, QPen, QUndoStack
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QSpinBox, QDoubleSpinBox, QGraphicsScene, QGraphicsView,
    QInputDialog, QColorDialog, QMessageBox, QFileDialog, QScrollArea, QFrame, QPlainTextEdit, QApplication)

from notepadview.theme import ThemedFontComboBox as QFontComboBox, ThemedMenu as QMenu

from notepadview.pdf_model import PdfModel
from notepadview.image_editor import ImageChange, FontChoiceDelegate


class PdfView(QGraphicsView):
    def contextMenuEvent(self, event):
        editor = self.parent()
        editor.select_at(self.mapToScene(event.pos()))
        menu = editor.style_menu()
        menu.exec(event.globalPos())
        menu.deleteLater()
        event.accept()

    def mousePressEvent(self, event):
        editor = self.parent()
        if event.button() == Qt.LeftButton:
            editor.end_drag()
            self.press_position = event.position().toPoint()
            self._drag_attempted = False
            self.start = self.mapToScene(event.position().toPoint())
            editor.select_at(self.start)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        editor = self.parent()
        if (hasattr(self, 'start') and event.buttons() & Qt.LeftButton
                and not self._drag_attempted
                and getattr(editor, '_drag_item', None) is None
                and (event.position().toPoint() - self.press_position).manhattanLength()
                >= QApplication.startDragDistance()):
            self._drag_attempted = True
            editor._safe(editor.begin_drag)
        if getattr(editor, '_drag_item', None) is not None and event.buttons() & Qt.LeftButton:
            delta = self.mapToScene(event.position().toPoint()) - self.start
            editor._drag_item.setPos(delta)
            if editor._selection_item is not None:
                editor._selection_item.setPos(delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() != Qt.LeftButton or not hasattr(self, 'start'):
            return
        end = self.mapToScene(event.position().toPoint())
        start = self.start
        editor = self.parent()
        del self.start
        try:
            if getattr(editor, '_drag_item', None) is not None:
                editor.gesture(start, end)
        finally:
            editor.end_drag()

    def mouseDoubleClickEvent(self, event):
        super().mouseDoubleClickEvent(event)
        if event.button() != Qt.LeftButton:
            return
        editor = self.parent()
        if hasattr(self, 'start'):
            del self.start
        editor.end_drag()
        editor.select_at(self.mapToScene(event.position().toPoint()))
        selection, page = editor.selected, editor.page
        if selection is not None:
            QTimer.singleShot(0, editor, lambda: editor._safe(editor.edit_selection)
                              if editor.selected == selection and editor.page == page else None)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and hasattr(self, 'start'):
            del self.start
            self.parent().end_drag()
            event.accept()
            return
        super().keyPressEvent(event)


class PdfEditor(QWidget):
    changed = Signal()
    image_format = 'pdf'

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.setObjectName('imageEditor')
        self.undo_stack = QUndoStack(self)
        self.model = PdfModel.from_snapshot(state)
        self.page = 0
        self.selected = None
        self.fill = '#222222'
        self._fitted = True
        self._scale = 1.0
        self._state = deepcopy(state)
        self._render_key = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)
        panel = QWidget()
        panel.setObjectName('pdfToolsPanel')
        tools = QHBoxLayout(panel)
        tools.setContentsMargins(12, 10, 12, 10)
        tools.setSpacing(8)
        def button(label, callback, tip=None):
            item = QPushButton(label)
            item.setToolTip(tip or label)
            item.clicked.connect(lambda checked=False: self._safe(callback))
            tools.addWidget(item)
            return item
        self.text_font = QFont('Arial', 16)
        self.edit_button = button('编辑文字', self.edit_selection, '单击选择文字，双击也可编辑')
        self.add_button = button('新增文字', self.prompt_add_text, '输入后放入当前页，可拖动调整位置')
        self.delete_button = button('删除文字', self.delete_selection, '删除选中的新增文字或原有文字块')
        tools.addWidget(QLabel('字体'))
        self.font_box = QFontComboBox()
        self.font_box.setFixedWidth(160)
        self.font_box.setCurrentFont(self.text_font)
        self.font_box.setStyleSheet('font-size: 12px;')
        self.font_box.setItemDelegate(FontChoiceDelegate(self.font_box))
        tools.addWidget(self.font_box)
        tools.addWidget(QLabel('字号'))
        self.size_box = QDoubleSpinBox()
        self.size_box.setRange(1, 300)
        self.size_box.setValue(16)
        self.size_box.setSuffix(' pt')
        self.size_box.setFixedWidth(100)
        self.size_box.setKeyboardTracking(False)
        tools.addWidget(self.size_box)
        self.font_box.currentFontChanged.connect(lambda font: self._safe(lambda: self.set_font_style(family=font.family())))
        self.size_box.valueChanged.connect(lambda size: self._safe(lambda: self.set_font_style(size=size)))
        self.color_button = button('颜色', self.choose_color, '修改选中文字的颜色；未选择时设置新增文字颜色')
        tools.addStretch()
        scroll = QScrollArea()
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setObjectName('pdfToolbarScroll')
        scroll.setFixedHeight(76)
        scroll.setFrameShape(QFrame.NoFrame)
        layout.addWidget(scroll)
        self.hint = QLabel('单击选择文字，双击编辑；新增文字可拖动。扫描页不支持 OCR。')
        self.hint.setObjectName('imageEditorHint')
        self.hint.setStyleSheet('font-size: 14px;')
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet('font-size: 14px; color: #c77700;')
        self.hint.setText('注：PDF文档只能做简单编辑。单击选择文字，双击编辑；文字可拖动。扫描页不支持 OCR。')
        layout.addWidget(self.hint)
        self.pdf_notice = self.hint
        self.scene = QGraphicsScene(self)
        self.view = PdfView(self)
        self.view.setObjectName('imageCanvas')
        self.view.setScene(self.scene)
        layout.addWidget(self.view, 1)
        navigation = QWidget(self)
        navigation.setObjectName('pdfNavigation')
        navigation_layout = QHBoxLayout(navigation)
        navigation_layout.setContentsMargins(10, 6, 10, 6)
        navigation_layout.setSpacing(10)
        self.previous_button = QPushButton('‹  上一页')
        self.previous_button.clicked.connect(lambda: self._safe(lambda: self.go_page(self.page - 1)))
        navigation_layout.addWidget(self.previous_button)
        self.page_box = QSpinBox()
        self.page_box.setFixedWidth(78)
        self.page_box.setAlignment(Qt.AlignCenter)
        self.page_box.setButtonSymbols(QSpinBox.NoButtons)
        self.page_box.setToolTip('输入页码后按回车跳转')
        self.page_box.setAccessibleName('当前页码')
        self.page_box.setKeyboardTracking(False)
        self.page_box.valueChanged.connect(lambda value: self._safe(lambda: self.go_page(value - 1)))
        navigation_layout.addWidget(self.page_box)
        self.total = QLabel()
        navigation_layout.addWidget(self.total)
        self.next_button = QPushButton('下一页  ›')
        self.next_button.clicked.connect(lambda: self._safe(lambda: self.go_page(self.page + 1)))
        navigation_layout.addWidget(self.next_button)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.addStretch()
        bottom.addWidget(navigation)
        bottom.addStretch()
        layout.addLayout(bottom)
        model = self.model
        self.destroyed.connect(model.close)
        self._refresh()
        self.undo_stack.indexChanged.connect(self._history_changed)
        self.undo_stack.cleanChanged.connect(self._history_changed)

    @Slot()
    def _history_changed(self):
        self.changed.emit()

    @classmethod
    def from_path(cls, path, parent=None):
        model = PdfModel.from_path(path)
        try:
            return cls(model.snapshot(), parent)
        finally:
            model.close()

    @classmethod
    def from_snapshot(cls, snapshot, parent=None):
        editor = cls(snapshot['initial'], parent)
        legacy = snapshot.get('version') != 2
        for change in snapshot.get('history', []):
            before = editor.model.checkpoint()
            start = editor.model._position
            operations = ([{'method': 'restore_state', 'args': [change['after']], 'kwargs': {}}]
                          if legacy else change['operations'])
            for operation in operations:
                if operation['method'] not in {'add_text', 'update_layer', 'remove_layer', 'annotate',
                        'replace_text', 'set_field', 'rotate_page', 'delete_page', 'move_page',
                        'insert_pages', 'insert_data', 'remove_annotation', 'restore_state'}:
                    raise ValueError('Unsupported PDF history operation')
                if operation['method'] == 'insert_pages' and (len(operation['args']) > 1 or operation['kwargs'].get('path') is not None):
                    raise ValueError('PDF history must embed inserted content')
                editor.model.replay_operation(operation)
            command = ImageChange(editor, before, editor.model.checkpoint(), change['label'])
            command.operations = deepcopy(editor.model.operations[start:editor.model._position])
            editor.undo_stack.push(command)
        clean = snapshot.get('clean', 0)
        editor._loading_history = True
        editor._pending_restore = None
        try:
            if clean >= 0:
                editor.undo_stack.setIndex(clean)
                editor.undo_stack.setClean()
            else:
                editor.undo_stack.resetClean()
            editor.undo_stack.setIndex(snapshot.get('index', 0))
        finally:
            editor._loading_history = False
        if editor._pending_restore is not None:
            editor._restore(editor._pending_restore)
        editor._pending_restore = None
        view = snapshot.get('view', {})
        editor.page = max(0, min(editor.model.page_count - 1, view.get('page', 0)))
        editor._fitted = view.get('fit', True)
        editor._scale = max(0.05, min(8, view.get('scale', 1)))
        editor._render_key = None
        editor._refresh()
        return editor

    def snapshot(self):
        history = []
        for i in range(self.undo_stack.count()):
            command = self.undo_stack.command(i)
            history.append(dict(operations=deepcopy(command.operations), label=command.text()))
        return {'version': 2, 'initial': deepcopy(self._state),
                'history': history, 'index': self.undo_stack.index(), 'clean': self.undo_stack.cleanIndex(),
                'view': {'page': self.page, 'fit': self._fitted, 'scale': self._scale}}

    def _safe(self, callback):
        try:
            return callback()
        except (ValueError, OSError, RuntimeError) as error:
            QMessageBox.warning(self, 'PDF 编辑', str(error))

    def _restore(self, state):
        if getattr(self, '_loading_history', False):
            self._pending_restore = state
            return
        if 'data' in state:
            self.model.close()
            self.model = PdfModel.from_snapshot(state)
            self.destroyed.connect(self.model.close)
        else:
            self.model.restore_checkpoint(state)
        self._render_key = None
        self.page = min(self.page, self.model.page_count - 1)
        self.selected = None
        self._refresh()

    def change(self, label, callback):
        before = self.model.checkpoint()
        start = self.model._position
        selection = self.selected
        try:
            callback()
            after = self.model.checkpoint()
        except Exception:
            self._restore(before)
            self.selected = selection
            self._sync_selection_style()
            self._selection_box()
            raise
        if before != after:
            command = ImageChange(self, before, after, label)
            command.operations = deepcopy(self.model.operations[start:self.model._position])
            self.undo_stack.push(command)

    def _refresh(self):
        key = (id(self.model), self.page, self.model._position, repr(self.model.layers))
        if key == self._render_key:
            self._selection_box()
            return
        self._selection_item = None
        pix, width, height = self.model.render(self.page)
        image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
        self.scene.clear()
        self._drag_item = None
        self._render_key = key
        item = self.scene.addPixmap(QPixmap.fromImage(image))
        self._page_item = item
        item.setScale(width / pix.width)
        self.scene.setSceneRect(0, 0, width, height)
        self.page_box.blockSignals(True)
        self.page_box.setRange(1, self.model.page_count)
        self.page_box.setValue(self.page + 1)
        self.page_box.blockSignals(False)
        self.total.setText('/ ' + str(self.model.page_count) + ' 页')
        self.previous_button.setEnabled(self.page > 0)
        self.next_button.setEnabled(self.page < self.model.page_count - 1)
        self._selection_box()
        if self._fitted:
            self.fit()
        else:
            self.view.resetTransform()
            self.view.scale(self._scale, self._scale)

    def _selection_box(self):
        editable = self.selected is not None and self.selected[0] in ('layer', 'block')
        self.edit_button.setEnabled(editable)
        self.delete_button.setEnabled(editable)
        if getattr(self, '_selection_item', None) is not None:
            self.scene.removeItem(self._selection_item)
            self._selection_item = None
        if self.selected is None:
            return
        kind, index = self.selected
        if kind == 'layer':
            rect = self.model.layers[index]['rect']
        else:
            entries = self.model.text_blocks(self.page) if kind == 'block' else self.model.fields(self.page) if kind == 'field' else self.annotations()
            entry = next((e for e in entries if e['id'] == index), None)
            if entry is None:
                return
            rect = entry['rect']
        self._selection_item = self.scene.addRect(QRectF(*self._xywh(self.model.display_rect(self.page, rect))), QPen(Qt.blue, 1, Qt.DashLine))

    @staticmethod
    def _xywh(rect):
        return rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1]

    def annotations(self):
        with self.model.open() as doc:
            page = doc[self.page]
            return [{'id': a.xref, 'rect': list(a.rect)} for a in page.annots() or []]

    def select_at(self, point):
        point = fitz.Point(self.model.to_page(self.page, (point.x(), point.y())))
        self.selected = None
        self.hint.setText('注：PDF文档只能做简单编辑。单击选择文字，双击编辑；文字可拖动。扫描页不支持 OCR。')
        for index in reversed(range(len(self.model.layers))):
            layer = self.model.layers[index]
            if layer['page'] == self.page and fitz.Rect(layer['rect']).contains(point):
                self.selected = ('layer', index)
                self.fill = layer['fill']
                break
        if self.selected is None:
            blocks = self.model.text_blocks(self.page)
            if not blocks:
                self.hint.setText('此页没有可提取文字，可能是扫描件；不支持 OCR，可新增文字。')
            for block in reversed(blocks):
                if fitz.Rect(block['rect']).contains(point):
                    self.selected = ('block', block['id'])
                    break
        self._sync_selection_style()
        self._refresh()
        return self.selected is not None

    def _sync_selection_style(self):
        if not self.selected or self.selected[0] not in ('layer', 'block'):
            return
        kind, index = self.selected
        if kind == 'layer':
            layer = self.model.layers[index]
            family, size, self.fill = layer['font_family'], layer['font_size'], layer['fill']
        else:
            span = self.model.text_details(self.page)[index]['lines'][0]['spans'][0]
            family, size, self.fill = span['font'], span['size'], '#%06x' % span['color']
        self.text_font = QFont(family)
        self.text_font.setPointSizeF(size)
        self.font_box.blockSignals(True)
        self.size_box.blockSignals(True)
        try:
            self.font_box.setCurrentFont(self.text_font)
            self.size_box.setValue(size)
        finally:
            self.font_box.blockSignals(False)
            self.size_box.blockSignals(False)

    def begin_drag(self):
        if not self.selected or self.selected[0] not in ('layer', 'block'):
            return
        index = self.selected[1]
        # Rasterize once; pointer moves only translate a transparent graphics item.
        if self.selected[0] == 'layer':
            pix = self.model.render_layer(index)
            background, width, _ = self.model.render(self.page, exclude_layer=index)
        else:
            entry = next(b for b in self.model.text_blocks(self.page) if b['id'] == index)
            rect = fitz.Rect(entry['rect'])
            with fitz.open() as scratch:
                scratch.insert_pdf(self.model._doc, from_page=self.page, to_page=self.page)
                target = scratch[0]
                pix = self.model.render_text_block(self.page, index)
                background_model = PdfModel(scratch.tobytes())
                try:
                    background_model.remove_text_block(0, index)
                    scratch.delete_page(0)
                    scratch.insert_pdf(background_model._doc)
                finally:
                    background_model.close()
                target = scratch[0]
                from notepadview.pdf_model import insert_text
                for layer in self.model.layers:
                    if layer['page'] == self.page:
                        insert_text(target, layer['rect'], layer['text'], layer['fill'],
                                    layer['font_family'], layer['font_size'])
                background = target.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                width = target.rect.width
        overlay = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGBA8888_Premultiplied).copy()
        image = QImage(background.samples, background.width, background.height,
                       background.stride, QImage.Format_RGB888).copy()
        self._page_item.setPixmap(QPixmap.fromImage(image))
        self._page_item.setScale(width / background.width)
        self._drag_item = self.scene.addPixmap(QPixmap.fromImage(overlay))
        self._drag_item.setScale(1 / 1.5)
        self._drag_item.setOffset(pix.x, pix.y)
        self._drag_item.setZValue(1)
        if self._selection_item is not None:
            self._selection_item.setZValue(2)

    def end_drag(self):
        if getattr(self, '_drag_item', None) is not None:
            self._render_key = None
            self._refresh()

    def gesture(self, start, end):
        def action():
            if self.selected and self.selected[0] in ('layer', 'block'):
                first = self.model.to_page(self.page, (start.x(), start.y()))
                last = self.model.to_page(self.page, (end.x(), end.y()))
                dx, dy = last[0] - first[0], last[1] - first[1]
                if abs(dx) + abs(dy) > 1:
                    if self.selected[0] == 'layer':
                        layer = self.model.layers[self.selected[1]]
                        self.move_text(layer['rect'][0] + dx, layer['rect'][1] + dy)
                    else:
                        index = self.selected[1]
                        entry = next(b for b in self.model.text_blocks(self.page) if b['id'] == index)
                        self.change('移动 PDF 原文', lambda: self.model.replace_text(
                            self.page, index, entry['text'], offset=(dx, dy)))
                        rect = fitz.Rect(entry['rect']) + (dx, dy, dx, dy)
                        self._select_original(rect)
                        self._refresh()
        self._safe(action)

    def text_dialog(self, title, label, text=''):
        dialog = QInputDialog(self)
        dialog.setWindowTitle(title)
        dialog.setLabelText(label)
        dialog.setOption(QInputDialog.UsePlainTextEditForTextInput)
        dialog.setTextValue(text)
        dialog.resize(720, 460)
        dialog.setMinimumSize(640, 400)
        field = dialog.findChild(QPlainTextEdit)
        field.setFont(self.text_font)
        try:
            accepted = dialog.exec() == QInputDialog.Accepted
            return dialog.textValue(), accepted
        finally:
            dialog.deleteLater()

    def prompt_add_text(self):
        text, ok = self.text_dialog('新增文字', '文字内容')
        if ok and text.strip():
            with self.model.open() as doc:
                page = doc[self.page]
                x = min(30, page.cropbox.width * 0.1)
                y = min(100, page.cropbox.height * 0.2)
            self.add_text(text, x, y)

    def add_text(self, text, x=30, y=100):
        with self.model.open() as doc:
            page = doc[self.page]
            width, height = page.cropbox.width, page.cropbox.height
        self.change('添加 PDF 文字', lambda: self.model.add_text(self.page, (x, y, min(width, x + 260), min(height, y + 80)), text, self.fill, self.text_font.family(), self.text_font.pointSizeF()))
        self.selected = ('layer', len(self.model.layers) - 1)
        self._refresh()

    def move_text(self, x, y):
        if not self.selected or self.selected[0] != 'layer':
            return
        index = self.selected[1]
        rect = fitz.Rect(self.model.layers[index]['rect'])
        area = [x, y, x + rect.width, y + rect.height]
        self.change('移动 PDF 文字', lambda: self.model.update_layer(index, rect=area))
        self.selected = ('layer', index)
        self._refresh()

    def style_menu(self):
        menu = QMenu(self)
        editable = self.selected is not None and self.selected[0] in ('layer', 'block')
        copy = menu.addAction('复制文字样式')
        copy.setEnabled(editable)
        copy.triggered.connect(lambda: self._safe(self.copy_text_style))
        apply = menu.addAction('应用文字样式')
        apply.setEnabled(editable and getattr(self, '_copied_text_style', None) is not None)
        apply.triggered.connect(lambda: self._safe(self.apply_text_style))
        return menu

    def copy_text_style(self):
        if not self.selected or self.selected[0] not in ('layer', 'block'):
            return
        kind, index = self.selected
        if kind == 'layer':
            layer = self.model.layers[index]
            style = (layer['font_family'], layer['font_size'], layer['fill'])
        else:
            with self.model.open() as doc:
                block = self.model.text_details(self.page)[index]
                span = block['lines'][0]['spans'][0]
                style = (span['font'], span['size'], '#%06x' % span['color'])
        self._copied_text_style = style
        self.hint.setText('已复制文字样式，右键其他文字可应用。')

    def apply_text_style(self):
        style = getattr(self, '_copied_text_style', None)
        if style is None or not self.selected or self.selected[0] not in ('layer', 'block'):
            return
        family, size, fill = style
        kind, index = self.selected
        if kind == 'layer':
            self.change('应用 PDF 文字样式', lambda: self.model.update_layer(
                index, font_family=family, font_size=size, fill=fill))
            self.selected = ('layer', index)
        else:
            entry = next(b for b in self.model.text_blocks(self.page) if b['id'] == index)
            self.change('应用 PDF 文字样式', lambda: self.model.replace_text(
                self.page, index, entry['text'], family=family, size=size, fill=fill))
            self._select_original(entry['rect'])
        self.text_font = QFont(family)
        self.text_font.setPointSizeF(size)
        self.fill = fill
        self.font_box.blockSignals(True)
        self.size_box.blockSignals(True)
        self.font_box.setCurrentFont(self.text_font)
        self.size_box.setValue(size)
        self.font_box.blockSignals(False)
        self.size_box.blockSignals(False)
        self._refresh()

    def set_font_style(self, family=None, size=None):
        if family is not None:
            self.text_font.setFamily(family)
        if size is not None:
            self.text_font.setPointSizeF(size)
        if not self.selected:
            return
        kind, index = self.selected
        if kind == 'layer':
            changes = {}
            if family is not None:
                changes['font_family'] = family
            if size is not None:
                changes['font_size'] = size
            self.change('修改 PDF 文字样式', lambda: self.model.update_layer(index, **changes))
            self.selected = ('layer', index)
        elif kind == 'block':
            entry = next(b for b in self.model.text_blocks(self.page) if b['id'] == index)
            self.change('修改 PDF 原文样式', lambda: self.model.replace_text(
                self.page, index, entry['text'], family=family, size=size))
            self._select_original(entry['rect'])
        self._refresh()

    def set_style(self):
        if not self.selected:
            return
        kind, index = self.selected
        if kind == 'layer':
            if QColor(self.model.layers[index]['fill']) == QColor(self.fill):
                return
            self.change('修改 PDF 文字颜色', lambda: self.model.update_layer(index, fill=self.fill))
            self.selected = ('layer', index)
        elif kind == 'block':
            entry = next(b for b in self.model.text_blocks(self.page) if b['id'] == index)
            if self._original_color_matches(index):
                return
            self.change('修改 PDF 原文颜色', lambda: self.model.replace_text(self.page, index, entry['text'], fill=self.fill))
            self._select_original(entry['rect'])
        self._refresh()

    def _original_color_matches(self, index):
        ink = QColor(self.fill)
        block = self.model.text_details(self.page)[index]
        if not all(span['color'] == ink.rgb() & 0xffffff
                   for line in block['lines'] for span in line['spans']):
            return False
        keys = self.model.text_character_keys(self.page, index)
        wanted = tuple(channel / 255 for channel in ink.getRgb()[:3])
        for trace in self.model._doc[self.page].get_texttrace():
            if trace['type'] != 1 or not any((char[0], tuple(char[2])) in keys for char in trace['chars']):
                continue
            stroke = trace['color']
            if len(stroke) == 1:
                stroke = stroke * 3
            if len(stroke) != 3 or any(abs(a - b) > 0.5 / 255 + 1e-7 for a, b in zip(stroke, wanted)):
                return False
        return True

    def _select_original(self, rect):
        self.selected = None
        blocks = self.model.text_blocks(self.page)
        if blocks:
            block = max(blocks, key=lambda b: (fitz.Rect(b['rect']) & fitz.Rect(rect)).get_area())
            if (fitz.Rect(block['rect']) & fitz.Rect(rect)).get_area() > 0:
                self.selected = ('block', block['id'])

    def choose_color(self):
        value = QColorDialog.getColor(QColor(self.fill), self)
        if value.isValid():
            self.fill = value.name()
            self.set_style()

    def edit_selection(self):
        if self.selected is None:
            self.hint.setText('请先选择文字或表单字段；原文编辑仅支持可提取文字，不支持 OCR。')
            return
        kind, index = self.selected
        if kind == 'field':
            return self.edit_field(index)
        if kind == 'annot':
            return
        entry = self.model.layers[index] if kind == 'layer' else next(b for b in self.model.text_blocks(self.page) if b['id'] == index)
        text, ok = self.text_dialog('修改文字', '文字内容（保留原有样式）', entry['text'])
        if not ok or text == entry['text']:
            return
        if kind == 'layer':
            self.change('修改 PDF 文字', lambda: self.model.update_layer(index, text=text))
        else:
            self.change('替换 PDF 原有文字', lambda: self.model.replace_text(self.page, index, text))
            self._select_original(entry['rect'])
        if kind == 'layer':
            self.selected = ('layer', index)
        self._refresh()

    def edit_field(self, index):
        field = next(f for f in self.model.fields(self.page) if f['id'] == index)
        kind = field['kind']
        if field['flags'] & fitz.PDF_FIELD_IS_READ_ONLY:
            raise ValueError('此表单字段为只读。')
        if kind in (fitz.PDF_WIDGET_TYPE_CHECKBOX, fitz.PDF_WIDGET_TYPE_RADIOBUTTON):
            value, ok = QInputDialog.getItem(self, '填写表单', field['name'], ['未选中', '选中'], int(field['value'] != 'Off'), False)
            value = value == '选中'
        elif kind in (fitz.PDF_WIDGET_TYPE_COMBOBOX, fitz.PDF_WIDGET_TYPE_LISTBOX):
            if field['flags'] & fitz.PDF_CH_FIELD_IS_MULTI_SELECT:
                raise ValueError('暂不支持多选列表。')
            pairs = [(c[0], c[1]) if isinstance(c, (tuple, list)) else (c, c) for c in field['choices']]
            choices = [label for _, label in pairs]
            current = next((i for i, (export, label) in enumerate(pairs) if field['value'] in (export, label)), 0)
            value, ok = QInputDialog.getItem(self, '填写表单', field['name'], choices,
                current, bool(field['flags'] & fitz.PDF_CH_FIELD_IS_EDIT))
        elif kind == fitz.PDF_WIDGET_TYPE_TEXT:
            value, ok = QInputDialog.getMultiLineText(self, '填写表单', field['name'], str(field['value'] or ''))
        else:
            raise ValueError('不支持数字签名、XFA、按钮动作或复杂表单脚本。')
        if ok:
            self.change('填写 PDF 表单', lambda: self.model.set_field(self.page, index, value))

    def delete_selection(self):
        if self.selected is None:
            return
        kind, index = self.selected
        if kind == 'layer':
            self.change('移除 PDF 文字', lambda: self.model.remove_layer(index))
        elif kind == 'block':
            self.change('删除 PDF 原有文字', lambda: self.model.replace_text(self.page, index, ''))
        elif kind == 'annot':
            def remove():
                self.model.remove_annotation(self.page, index)
            self.change('移除 PDF 标注', remove)

    def page_operation(self, index):
        def action():
            if index == 1:
                self.change('插入空白页', lambda: self.model.insert_pages(self.page + 1))
            elif index == 2:
                path, _ = QFileDialog.getOpenFileName(self, '插入 PDF', '', 'PDF (*.pdf)', options=QFileDialog.DontUseNativeDialog)
                if path:
                    import base64
                    data = base64.b64encode(Path(path).read_bytes()).decode('ascii')
                    self.change('插入 PDF', lambda: self.model.insert_data(self.page + 1, data))
            elif index == 3:
                if QMessageBox.question(self, '删除页面', '删除当前页？可撤销。') == QMessageBox.Yes:
                    self.change('删除 PDF 页面', lambda: self.model.delete_page(self.page))
            elif index in (4, 5):
                self.rotate_page(90 if index == 4 else -90)
            elif index == 6:
                number, ok = QInputDialog.getInt(self, '页面排序', '移动到第几页', self.page + 1, 1, self.model.page_count)
                if ok:
                    old = self.page
                    self.change('移动 PDF 页面', lambda: self.model.move_page(old, number - 1))
                    self.go_page(number - 1)
        self._safe(action)

    def rotate_page(self, degrees):
        self.change('旋转 PDF 页面', lambda: self.model.rotate_page(self.page, degrees))

    def go_page(self, page):
        self.page = max(0, min(self.model.page_count - 1, page))
        self.selected = None
        self._refresh()
        self.changed.emit()

    def zoom(self, factor):
        self._scale = max(0.05, min(8, self.view.transform().m11() * factor))
        self._fitted = False
        self.view.resetTransform()
        self.view.scale(self._scale, self._scale)
        self.changed.emit()

    def fit_and_notify(self):
        self.fit()
        self.changed.emit()

    def fit(self):
        self._fitted = True
        self.view.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)

    def showEvent(self, event):
        super().showEvent(event)
        self.fit()
        QTimer.singleShot(0, self, self.fit)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fitted:
            self.fit()

    def save(self, path):
        if Path(path).suffix.lower() != '.pdf':
            raise ValueError('PDF 必须保存为 .pdf 文件。')
        data = self.model.export()
        target = QSaveFile(str(path))
        if not target.open(QIODevice.WriteOnly):
            raise OSError(target.errorString())
        if target.write(data) != len(data):
            target.cancelWriting()
            raise OSError(target.errorString())
        if not target.commit():
            raise OSError(target.errorString())
