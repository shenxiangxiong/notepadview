"""请求配置数据控件；文本接口用于已有调用方的数据交换。"""
import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QComboBox, QLineEdit, QPlainTextEdit, QStackedWidget,
    QLabel, QFileDialog, QFormLayout, QHeaderView, QAbstractItemView, QTabBar,
)


class PairEditor(QWidget):
    textChanged = Signal()

    def __init__(self, *, mapping=False, multipart=False, compact=False, title=''):
        super().__init__()
        self.mapping = mapping
        self.multipart = multipart
        self.compact = compact
        self._invalid = None
        self._loading = False
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 4 if multipart else 3)
        self.table.setHorizontalHeaderLabels(
            ['启用', '名称', '值 / 文件路径', '类型'] if multipart else ['启用', '名称', '值'])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemChanged.connect(self._changed)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        self.add_button = QPushButton('添加')
        self.remove_button = QPushButton('删除选中行')
        self.add_button.clicked.connect(lambda: self.add_row())
        self.remove_button.clicked.connect(self.remove_rows)
        row.addWidget(self.add_button)
        row.addWidget(self.remove_button)
        if multipart and not compact:
            choose = QPushButton('选择文件…')
            choose.clicked.connect(self.choose_file)
            row.addWidget(choose)
        row.addStretch()
        layout.addLayout(row)
        if compact:
            self.setObjectName('apiCompactPairEditor')
            layout.setContentsMargins(8, 10, 8, 0)
            layout.setSpacing(0)
            if title:
                label = QLabel(title)
                label.setContentsMargins(6, 0, 0, 8)
                layout.insertWidget(0, label)
            self.table.setHorizontalHeaderLabels(
                ['', '参数名', '参数值', '类型'] if multipart else ['', '参数名', '参数值'])
            self.table.verticalHeader().hide()
            self.table.verticalHeader().setDefaultSectionSize(30)
            self.table.setShowGrid(False)
            self.table.setFrameShape(QTableWidget.NoFrame)
            self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
            self.table.setColumnWidth(0, 30)
            for column in (1, 2):
                self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.Stretch)
            if multipart:
                header = self.table.horizontalHeader()
                header.setSectionResizeMode(3, QHeaderView.Fixed)
                self.table.setColumnWidth(3, 100)
                header.moveSection(3, 2)
            self.table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
            layout.removeWidget(self.table)
            layout.insertWidget(1 if title else 0, self.table)
            self.add_button.setText('添加参数')
            self.add_button.setFlat(True)
            self.add_button.setCursor(Qt.PointingHandCursor)
            self.add_button.setStyleSheet('QPushButton { text-align:left; padding:5px 40px; border:0; }')
            row.removeWidget(self.add_button)
            layout.insertWidget(2 if title else 1, self.add_button)
            layout.addStretch(1)
            self.table.setSizeAdjustPolicy(QAbstractItemView.AdjustToContents)
            self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self.table.model().rowsInserted.connect(self._fit_rows)
            self.table.model().rowsRemoved.connect(self._fit_rows)
            self._fit_rows()
            self.remove_button.hide()
            self.table.setContextMenuPolicy(Qt.ActionsContextMenu)
            from PySide6.QtGui import QAction
            delete_action = QAction('删除选中参数', self.table)
            delete_action.triggered.connect(self.remove_rows)
            self.table.addAction(delete_action)

    def setReadOnly(self, readonly):
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers if readonly else
                                   QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.add_button.setVisible(not readonly)
        self.remove_button.setVisible(not readonly)
        for action in self.table.actions():
            action.setEnabled(not readonly)
        self.table.setColumnHidden(0, readonly)
        self._readonly = readonly
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if readonly:
                item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)

    def _fit_rows(self, *_):
        self.table.setFixedHeight(self.table.horizontalHeader().height() +
                                  min(self.table.rowCount(), 8) * 30 + 2)

    def _changed(self, *_):
        if not self._loading:
            self._invalid = None
            self.textChanged.emit()

    def add_row(self, value=None):
        value = value or {'key': '', 'value': ''}
        was_loading = self._loading
        self._loading = True
        index = self.table.rowCount()
        self.table.insertRow(index)
        enabled = QTableWidgetItem()
        enabled.setCheckState(Qt.Unchecked if value.get('disabled') else Qt.Checked)
        self.table.setItem(index, 0, enabled)
        self.table.setItem(index, 1, QTableWidgetItem(str(value.get('key', ''))))
        content = value.get('src', '') if value.get('type') == 'file' else value.get('value', '')
        item = QTableWidgetItem(json.dumps(content, ensure_ascii=False) if isinstance(content, list) else str(content))
        item.setData(Qt.UserRole, content)
        self.table.setItem(index, 2, item)
        if self.multipart:
            selector = QComboBox()
            selector.addItems(['text', 'file'])
            selector.setCurrentText(value.get('type', 'text'))
            selector.currentTextChanged.connect(self._changed)
            self.table.setCellWidget(index, 3, selector)
            if self.compact:
                def update_upload(mode):
                    row_index = next((row for row in range(self.table.rowCount())
                                      if self.table.cellWidget(row, 3) is selector), -1)
                    if row_index < 0:
                        return
                    if mode == 'file':
                        upload = QPushButton('上传文件…')
                        upload.setToolTip(item.text())
                        def choose():
                            paths, _ = QFileDialog.getOpenFileNames(self, '选择上传文件')
                            if paths:
                                item.setText(json.dumps(paths, ensure_ascii=False))
                                item.setData(Qt.UserRole, paths)
                                upload.setText('; '.join(paths))
                                upload.setToolTip('; '.join(paths))
                        upload.clicked.connect(choose)
                        if item.text():
                            upload.setText(item.text())
                        self.table.setCellWidget(row_index, 2, upload)
                    else:
                        self.table.removeCellWidget(row_index, 2)
                selector.currentTextChanged.connect(update_upload)
                update_upload(selector.currentText())
        self._loading = was_loading
        if getattr(self, '_readonly', False):
            self.setReadOnly(True)
        self._changed()

    def remove_rows(self):
        rows = {item.row() for item in self.table.selectedItems()}
        for row in sorted(rows, reverse=True):
            self.table.removeRow(row)
        self._changed()

    def choose_file(self):
        paths, _ = QFileDialog.getOpenFileNames(self, '选择上传文件')
        if paths:
            self.add_row({'key': 'file', 'type': 'file', 'src': paths})

    def setPlainText(self, text):
        self._loading = True
        self.table.setRowCount(0)
        self._invalid = None
        try:
            data = json.loads(text or ('{}' if self.mapping else '[]'))
            if self.mapping:
                if not isinstance(data, dict):
                    raise ValueError()
                data = [{'key': k, 'value': v} for k, v in data.items()]
            elif isinstance(data, dict):
                data = [{'key': k, 'value': v} for k, v in data.items()]
            if not isinstance(data, list) or any(not isinstance(v, dict) for v in data):
                raise ValueError()
            for value in data:
                self.add_row(value)
        except (ValueError, TypeError):
            self._invalid = text
        self._loading = False
        self.textChanged.emit()

    def toPlainText(self):
        if self._invalid is not None:
            return self._invalid
        values = []
        for row in range(self.table.rowCount()):
            def text(col):
                item = self.table.item(row, col)
                return item.text() if item else ''
            entry = {'key': text(1), 'value': text(2)}
            if self.table.item(row, 0).checkState() != Qt.Checked:
                entry['disabled'] = True
            if self.multipart:
                entry['type'] = self.table.cellWidget(row, 3).currentText()
                if entry['type'] == 'file':
                    original = self.table.item(row, 2).data(Qt.UserRole)
                    entry['src'] = original if isinstance(original, list) and text(2) == json.dumps(original, ensure_ascii=False) else text(2)
                    entry.pop('value')
            values.append(entry)
        result = {v['key']: v['value'] for v in values if not v.get('disabled')} if self.mapping else values
        return json.dumps(result, ensure_ascii=False, indent=2)


class AuthEditor(QWidget):
    textChanged = Signal()

    def __init__(self):
        super().__init__()
        self._invalid = None
        self._extra = {}
        layout = QFormLayout(self)
        self.mode = QComboBox()
        self.mode.addItems(['none', 'bearer', 'basic', 'api_key'])
        layout.addRow('认证类型', self.mode)
        self.fields = {}
        for key, label in [('token', 'Token'), ('username', '用户名'), ('password', '密码'), ('key', '参数名称'), ('value', '参数值')]:
            field = QLineEdit()
            if key in ('token', 'password', 'value'):
                field.setEchoMode(QLineEdit.Password)
            self.fields[key] = field
            layout.addRow(label, field)
            field.textChanged.connect(self._changed)
        self.location = QComboBox()
        self.location.addItems(['header', 'query'])
        layout.addRow('放置位置', self.location)
        self.location.currentTextChanged.connect(self._changed)
        self.mode.currentTextChanged.connect(self._mode_changed)
        self._mode_changed()

    def _changed(self, *_):
        self._invalid = None
        self.textChanged.emit()

    def _mode_changed(self, *_):
        self._invalid = None
        visible = {'none': (), 'bearer': ('token',), 'basic': ('username', 'password'), 'api_key': ('key', 'value')}.get(self.mode.currentText(), ())
        for key, field in self.fields.items():
            field.setVisible(key in visible)
            self.layout().labelForField(field).setVisible(key in visible)
        self.location.setVisible(self.mode.currentText() == 'api_key')
        self.layout().labelForField(self.location).setVisible(self.mode.currentText() == 'api_key')
        self.textChanged.emit()

    def setPlainText(self, text):
        try:
            data = json.loads(text or '{}')
            if not isinstance(data, dict):
                raise ValueError()
            self._invalid = None
            self._extra = data
            self.mode.setCurrentText(data.get('type', 'none'))
            for key, field in self.fields.items():
                field.setText(str(data.get(key, '')))
            self.location.setCurrentText(data.get('in', 'header'))
        except (ValueError, TypeError):
            self._invalid = text
        self.textChanged.emit()

    def toPlainText(self):
        if self._invalid is not None:
            return self._invalid
        mode = self.mode.currentText()
        keys = {'none': (), 'bearer': ('token',), 'basic': ('username', 'password'), 'api_key': ('key', 'value')}[mode]
        data = {'type': mode, **{key: self.fields[key].text() for key in keys}}
        if mode == 'api_key':
            data['in'] = self.location.currentText()
        return json.dumps(data, ensure_ascii=False, indent=2)


class BodyEditor(QWidget):
    textChanged = Signal()

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        self.mode = QComboBox()
        self.mode.addItems(['none', 'raw', 'form', 'multipart', 'binary'])
        self.mode.setParent(self)
        self.mode.hide()
        self.mode_tabs = QTabBar()
        self.mode_tabs.setObjectName('apiBodyTypes')
        self.mode_tabs.setExpanding(False)
        self.mode_tabs.setDrawBase(False)
        self._tab_modes = ['none', 'multipart', 'form', 'raw', 'binary']
        for label in ('none', 'form-data', 'x-www-form-urlencoded', 'Text', 'Binary'):
            self.mode_tabs.addTab(label)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.mode_tabs)
        self.mode_tabs.currentChanged.connect(
            lambda index: self.mode.setCurrentText(self._tab_modes[index]))
        self.mode.currentTextChanged.connect(
            lambda mode: self.mode_tabs.setCurrentIndex(self._tab_modes.index(mode)))
        self.stack = QStackedWidget()
        self.raw = QPlainTextEdit()
        self.form = PairEditor(compact=True)
        self.multipart = PairEditor(multipart=True, compact=True)
        binary = QWidget()
        row = QHBoxLayout(binary)
        self.path = QLineEdit()
        choose = QPushButton('选择文件…')
        choose.clicked.connect(self.choose_file)
        row.addWidget(self.path)
        row.addWidget(choose)
        for widget in (QLabel('此请求不发送 Body'), self.raw, self.form, self.multipart, binary):
            self.stack.addWidget(widget)
        layout.addWidget(self.stack)
        self.mode.currentIndexChanged.connect(self.stack.setCurrentIndex)
        self.mode.currentIndexChanged.connect(self.textChanged)
        for editor in (self.raw, self.form, self.multipart):
            editor.textChanged.connect(self.textChanged)
        self.path.textChanged.connect(self.textChanged)
        self._pending = ''
        self.mode.currentTextChanged.connect(self._apply_pending)

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择二进制文件')
        if path:
            self.path.setText(path)

    def _apply_pending(self, *_):
        if self._pending is not None:
            self._set_value(self._pending)
            self._pending = None

    def _set_value(self, text):
        mode = self.mode.currentText()
        if mode == 'binary':
            self.path.setText(text)
        elif mode in ('form', 'multipart'):
            getattr(self, mode).setPlainText(text or '[]')
        else:
            self.raw.setPlainText(text)

    def setPlainText(self, text):
        self._pending = text if self.mode.currentText() == 'none' else None
        self._set_value(text)
        self.textChanged.emit()

    def toPlainText(self):
        mode = self.mode.currentText()
        if mode == 'none':
            return self.raw.toPlainText()
        if mode == 'binary':
            return self.path.text()
        return getattr(self, mode).toPlainText()
