"""Two-level virtual categories and document operation entry points."""
import json

from PySide6.QtCore import QMimeData, QSignalBlocker, QSize, Qt
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView, QInputDialog,
                              QMenu, QStyle, QTreeWidget, QTreeWidgetItem)


class FileTree(QTreeWidget):
    MIME_TYPE = 'application/x-notepad-view-sessions'

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setObjectName('fileTree')
        self.setColumnCount(2)
        self.setHeaderHidden(True)
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.header().setSectionResizeMode(1, QHeaderView.Fixed)
        self.setColumnWidth(1, 62)
        self.setIndentation(16)
        self.setIconSize(QSize(16, 16))
        self.setUniformRowHeights(True)
        self.setAnimated(False)
        self.setTextElideMode(Qt.ElideMiddle)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.setExpandsOnDoubleClick(False)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self.itemClicked.connect(lambda item, _: owner.activate_file_item(item))
        self.currentItemChanged.connect(lambda item, _: owner.activate_file_item(item) if item else None)
        self.itemChanged.connect(self._item_changed)
        self.itemExpanded.connect(lambda item: self._expanded(item, True))
        self.itemCollapsed.connect(lambda item: self._expanded(item, False))
        self._category_items = {}
        self._session_items = {}

    def edit(self, index, trigger=QAbstractItemView.AllEditTriggers, event=None):
        if index.column() != 0:
            return False
        return super().edit(index, trigger, event)

    def _item_changed(self, item, column):
        if column != 0 or item.parent() is not None:
            return
        category_id = item.data(1, Qt.UserRole)
        if category_id is not None and not self.owner.rename_category(category_id, item.text(0)):
            self.owner.refresh_file_list()

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton:
            item = self.itemAt(event.position().toPoint())
            session = item.data(0, Qt.UserRole) if item else None
            event.accept()
            if session is not None:
                self.owner.remove_sessions([session])
            return
        if event.button() == Qt.RightButton:
            with QSignalBlocker(self):
                super().mousePressEvent(event)
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton:
            event.accept()
            return
        if event.button() == Qt.RightButton:
            with QSignalBlocker(self):
                super().mouseReleaseEvent(event)
        else:
            super().mouseReleaseEvent(event)

    def _expanded(self, item, expanded):
        category_id = item.data(1, Qt.UserRole)
        for category in self.owner.categories:
            if category['id'] == category_id:
                category['expanded'] = expanded
                self.owner.schedule_workspace_save()

    def item_for_session(self, session):
        return self._session_items.get(session.id)

    def refresh(self, sessions, current):
        self.blockSignals(True)
        self.setUpdatesEnabled(False)
        try:
            categories = [{'id': '', 'name': self.owner.default_category_name, 'expanded': True},
                          *self.owner.categories]
            wanted = {category['id'] for category in categories}
            for category in categories:
                key = category['id']
                if key not in self._category_items:
                    item = QTreeWidgetItem(self)
                    item.setData(1, Qt.UserRole, key)
                    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDropEnabled
                                  | Qt.ItemIsEditable)
                    item.setSizeHint(0, QSize(0, 36))
                    item.setIcon(0, self.style().standardIcon(QStyle.SP_DirIcon))
                    font = item.font(0)
                    font.setBold(True)
                    item.setFont(0, font)
                    item.setExpanded(category.get('expanded', True))
                    self._category_items[key] = item
                self._category_items[key].setText(0, category['name'])
            active_ids = {session.id for session in sessions}
            for key in list(self._session_items):
                if key not in active_ids:
                    item = self._session_items.pop(key)
                    item.parent().removeChild(item)
            positions = {}
            for index, session in enumerate(sessions):
                parent = self._category_items.get(session.category_id, self._category_items[''])
                position = positions.get(session.category_id, 0)
                positions[session.category_id] = position + 1
                item = self._session_items.get(session.id)
                if item is None:
                    item = QTreeWidgetItem(parent)
                    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled)
                    item.setSizeHint(0, QSize(0, 34))
                    item.setIcon(0, self.style().standardIcon(QStyle.SP_FileIcon))
                    self._session_items[session.id] = item
                elif item.parent() is not parent:
                    item.parent().removeChild(item)
                    parent.insertChild(position, item)
                if parent.indexOfChild(item) != position:
                    parent.takeChild(parent.indexOfChild(item))
                    parent.insertChild(position, item)
                item.setData(0, Qt.UserRole, session)
                name = session.name
                dirty = ' •' if session.document.isModified() else ''
                item.setText(0, f'{index + 1}. {name}{dirty}')
                item.setText(1, session.kind.upper() if session.kind != 'text' else session.document_format.upper())
                item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
                item.setToolTip(0, str(session.path) if session.path else '未保存到文件 · 自动缓存')
            for key in list(self._category_items):
                if key not in wanted:
                    item = self._category_items.pop(key)
                    self.takeTopLevelItem(self.indexOfTopLevelItem(item))
            for row in range(self.topLevelItemCount()):
                number = 0
                item = self.topLevelItem(row)
                item.setText(1, str(item.childCount()))
                for child_row in range(item.childCount()):
                    number += 1
                    child = item.child(child_row)
                    session = child.data(0, Qt.UserRole)
                    dirty = ' •' if session.document.isModified() else ''
                    child.setText(0, f'{number}. {session.name}{dirty}')
            if current is not None:
                item = self.item_for_session(current)
                if item is not self.currentItem():
                    self.setCurrentItem(item)
        finally:
            self.setUpdatesEnabled(True)
            self.blockSignals(False)

    def selected_sessions(self):
        return [item.data(0, Qt.UserRole) for item in self.selectedItems()
                if item.data(0, Qt.UserRole) is not None]

    def current_category(self):
        item = self.currentItem()
        if item is None:
            return ''
        parent = item.parent() or item
        return parent.data(1, Qt.UserRole) or ''

    def request_category(self):
        name, accepted = QInputDialog.getText(self.owner, '新建分类', '分类名称')
        if accepted and name.strip():
            self.owner.create_category(name)

    def build_context_menu(self, item):
        menu = QMenu(self)
        session = item.data(0, Qt.UserRole) if item else None
        parent = (item.parent() or item) if item else None
        category_id = parent.data(1, Qt.UserRole) if parent else ''
        if session is not None:
            targets = self.selected_sessions() if item.isSelected() else [session]
            menu.addAction('重命名…', lambda: self.owner.request_rename_document(session))
            save_as = menu.addAction('另存为…', lambda: self.owner.save_as(session=session))
            save_as.setEnabled(session.kind in ('text', 'diagram') or session.kind == 'image' and session.image_editor is not None)
            folder = menu.addAction('打开所在文件夹', lambda: self.owner.open_document_folder(session))
            folder.setEnabled(session.path is not None)
            menu.addAction('移除', lambda: self.owner.remove_sessions(targets))
            classify = menu.addMenu('归类')
            for category in [{'id': '', 'name': self.owner.default_category_name}, *self.owner.categories]:
                action = classify.addAction(category['name'])
                action.triggered.connect(lambda _=False, key=category['id']: self.owner.move_sessions_to_category(targets, key))
        elif item is not None:
            menu.addAction('新建文档', lambda: self.owner.choose_new_document(category_id=category_id))
            select = menu.addAction('批量选择', lambda: self._select_category_files(item))
            select.setEnabled(item.childCount() > 0)
            if category_id:
                menu.addAction('删除分类', lambda: self.owner.delete_category(category_id))
        return menu

    def _select_category_files(self, parent):
        self.clearSelection()
        parent.setExpanded(True)
        for index in range(parent.childCount()):
            parent.child(index).setSelected(True)
        self.setFocus()

    def _context_menu(self, position):
        item = self.itemAt(position)
        if item is not None and not item.isSelected():
            with QSignalBlocker(self):
                self.clearSelection()
                self.setCurrentItem(item)
                item.setSelected(True)
        menu = self.build_context_menu(item)
        menu.exec(self.viewport().mapToGlobal(position))
        menu.deleteLater()

    def startDrag(self, supported_actions):
        sessions = self.selected_sessions()
        if not sessions:
            return
        mime = QMimeData()
        mime.setData(self.MIME_TYPE, json.dumps([session.id for session in sessions]).encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.MoveAction)

    def dragEnterEvent(self, event):
        if event.source() is self and event.mimeData().hasFormat(self.MIME_TYPE):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        if event.source() is not self or not event.mimeData().hasFormat(self.MIME_TYPE):
            event.ignore()
            return
        item = self.itemAt(event.position().toPoint())
        category = (item.parent() or item).data(1, Qt.UserRole) if item else ''
        ids = json.loads(bytes(event.mimeData().data(self.MIME_TYPE)))
        self.owner.move_sessions_to_category([session for session in self.owner.sessions if session.id in ids], category)
        event.setDropAction(Qt.MoveAction)
        event.accept()
