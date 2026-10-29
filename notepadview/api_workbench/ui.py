from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3
from uuid import uuid4
from urllib.parse import urlsplit

import httpx
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTabWidget,
    QScrollArea, QToolTip, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from notepadview.theme import ThemedComboBox as QComboBox, ThemedMenu as QMenu

from notepadview.api_workbench.models import (
    ApiCollection, Environment, RequestDefinition, RequestHistory,
)
from notepadview.api_workbench.openapi import import_openapi
from notepadview.api_workbench.requests import prepare_request
from notepadview.api_workbench.runner import (
    CancelToken, RequestResult, send_request,
)
from notepadview.api_workbench.sensitive import is_sensitive_header
from notepadview.api_workbench.scripts import ScriptResult, ScriptRunner
from notepadview.palettes import THEMES
from notepadview.control_icons import control_icon


@dataclass
class _ExecutionOutput:
    result: RequestResult | None
    request: RequestDefinition
    environment: Environment
    pre_script: ScriptResult | None = None
    post_script: ScriptResult | None = None
    error: str = ''
    cookies: dict[str, str] | None = None


class _ExecutionSignals(QObject):
    finished = Signal(object)


class _ApiExecutionWorker(QRunnable):
    def __init__(self, request, environment, collection_variables,
                 environment_cookies, cancel_token):
        super().__init__()
        self.request = request
        self.environment = environment
        self.collection_variables = collection_variables
        self.environment_cookies = environment_cookies
        self.cancel_token = cancel_token
        self.signals = _ExecutionSignals()

    def run(self):
        request = self.request
        environment = self.environment
        pre_result = None
        post_result = None
        try:
            script_runner = ScriptRunner(timeout_seconds=1)
            if request.pre_script:
                pre_result = script_runner.run_before(
                    request.pre_script,
                    request=request.to_dict(),
                    environment={
                        key: value['value'] for key, value in environment.variables.items()
                    },
                    variables={
                        key: value['value']
                        for key, value in {
                            **self.collection_variables, **request.variables}.items()
                    },
                )
                if pre_result.error:
                    raise ValueError(pre_result.error)
                request_variables = dict(request.variables)
                for key, value in pre_result.variable_updates.items():
                    request_variables[key] = {
                        'value': value,
                        'secret': request_variables.get(key, {}).get('secret', False),
                    }
                request_data = request.to_dict()
                request_data['variables'] = request_variables
                request = RequestDefinition.from_dict(request_data)
                environment_variables = dict(environment.variables)
                for key, value in pre_result.environment_updates.items():
                    environment_variables[key] = {
                        'value': value,
                        'secret': environment_variables.get(key, {}).get('secret', False),
                    }
                environment = Environment(
                    id=environment.id, name=environment.name, base_url=environment.base_url,
                    default_headers=environment.default_headers,
                    variables=environment_variables,
                )

            prepared = prepare_request(
                request, environment, environment_cookies=self.environment_cookies,
                collection_variables=self.collection_variables)
            result = send_request(
                prepared, timeout=30, cancel_token=self.cancel_token)
            cookies = dict(self.environment_cookies)
            cookies.update(request.cookies)
            if result.response is not None:
                cookies.update(result.response.cookies)
            if result.response is not None and request.post_script:
                post_result = script_runner.run_after(
                    request.post_script,
                    response={
                        'status_code': result.response.status_code,
                        'status': result.response.status,
                        'headers': result.response.headers,
                        'body': result.response.body,
                    },
                    environment={
                        key: value['value'] for key, value in environment.variables.items()
                    },
                    variables={
                        key: value['value']
                        for key, value in {
                            **self.collection_variables, **request.variables}.items()
                    },
                    request=request.to_dict(),
                )
                if post_result.environment_updates:
                    environment_variables = dict(environment.variables)
                    for key, value in post_result.environment_updates.items():
                        environment_variables[key] = {
                            'value': value,
                            'secret': environment_variables.get(key, {}).get('secret', False),
                        }
                    environment = Environment(
                        id=environment.id, name=environment.name, base_url=environment.base_url,
                        default_headers=environment.default_headers,
                        variables=environment_variables,
                    )
            output = _ExecutionOutput(
                result=result, request=request, environment=environment,
                pre_script=pre_result, post_script=post_result, cookies=cookies)
        except (ValueError, TypeError, OSError, RuntimeError, sqlite3.Error) as error:
            output = _ExecutionOutput(
                result=None, request=request, environment=environment,
                pre_script=pre_result, post_script=post_result, error=str(error))
        self.signals.finished.emit(output)


class _OpenApiFetchSignals(QObject):
    finished = Signal(object, object)


class _OpenApiFetchWorker(QRunnable):
    def __init__(self, url):
        super().__init__()
        self.url = url
        self.signals = _OpenApiFetchSignals()

    def run(self):
        parsed = urlsplit(self.url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            self.signals.finished.emit(None, 'URL 必须是有效的 HTTP 或 HTTPS 地址')
            return
        if parsed.username or parsed.password:
            self.signals.finished.emit(None, '导入 URL 不应在地址中包含用户名或密码')
            return
        source_name = f'{parsed.scheme}://{parsed.hostname}{parsed.path}'
        try:
            response = httpx.get(
                self.url, timeout=httpx.Timeout(15, connect=5), follow_redirects=True)
            response.raise_for_status()
            result = import_openapi(response.content, source_name=source_name)
        except (ValueError, RuntimeError) as error:
            self.signals.finished.emit(None, str(error))
            return
        except httpx.HTTPError as error:
            self.signals.finished.emit(
                None, f'无法获取 OpenAPI 文档（{type(error).__name__}）')
            return
        self.signals.finished.emit(result, None)


class ApiWorkbenchPage(QWidget):
    def __init__(self, store, parent=None, *, on_return=None):
        super().__init__(parent)
        self.store = store
        self.on_return = on_return
        self.theme = 'light'
        self.collections = {}
        self.environments = []
        self.current_collection = None
        self.current_request = None
        self.current_operation = None
        self._history_original_editors = None
        self._is_history_selection = False
        self._batch_delete_mode = False
        self._long_press_item = None
        self._long_press_activated = False
        self._long_press_timer = QTimer(self)
        self._long_press_timer.setSingleShot(True)
        self._long_press_timer.setInterval(600)
        self._long_press_timer.timeout.connect(self._enter_batch_delete_mode)
        self.thread_pool = QThreadPool(self)
        self.active_worker = None
        self.cancel_token = None
        self.setObjectName('apiWorkbench')
        self._build_ui()
        self._batch_delete_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self._batch_delete_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._batch_delete_shortcut.activated.connect(self._exit_batch_delete_mode)
        self._load_data()
        self._mark_clean()
        self.request_url_input.textChanged.connect(self._update_dirty)
        self.request_method_selector.currentTextChanged.connect(self._update_dirty)
        self.body_mode_selector.currentTextChanged.connect(self._update_dirty)
        for editor in self.request_editors.values():
            editor.textChanged.connect(self._update_dirty)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)
        toolbar = QHBoxLayout()
        self.return_button = QPushButton('返回文档')
        self.return_button.clicked.connect(self._return_to_documents)
        toolbar.addWidget(self.return_button)
        self.import_openapi_button = QPushButton('导入 OpenAPI')
        self.import_openapi_button.clicked.connect(self.choose_openapi_import)
        toolbar.addWidget(self.import_openapi_button)
        toolbar.addStretch(1)
        toolbar.addWidget(QLabel('环境'))
        self.environment_selector = QComboBox()
        self.environment_selector.setMinimumWidth(160)
        self.environment_selector.currentIndexChanged.connect(self._environment_changed)
        toolbar.addWidget(self.environment_selector)
        self.new_environment_button = QPushButton('新建环境')
        self.new_environment_button.clicked.connect(self.create_environment)
        toolbar.addWidget(self.new_environment_button)
        self.edit_environment_button = QPushButton('编辑环境')
        self.edit_environment_button.clicked.connect(self.edit_environment)
        toolbar.addWidget(self.edit_environment_button)
        root.addLayout(toolbar)

        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.setHandleWidth(10)
        tree_panel = QWidget()
        tree_layout = QVBoxLayout(tree_panel)
        tree_layout.setContentsMargins(0, 0, 4, 0)
        tree_layout.setSpacing(4)
        self.interface_root_row = QWidget(tree_panel)
        tree_toolbar = QHBoxLayout(self.interface_root_row)
        tree_toolbar.setContentsMargins(2, 2, 2, 2)
        tree_toolbar.setSpacing(2)
        self.interface_root_label = QLabel('接口管理')
        self.interface_root_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        tree_toolbar.addWidget(self.interface_root_label)
        tree_toolbar.addStretch(1)
        self.add_item_button = QPushButton()
        self.add_item_button.setIcon(control_icon('add', self.theme))
        self.add_item_button.setIconSize(QSize(16, 16))
        self.add_item_button.setAccessibleName('新增模块或接口')
        self.add_item_button.setStyleSheet(
            'QPushButton { padding: 0px; }'
            'QPushButton::menu-indicator { width: 0px; height: 0px; }')
        self.add_item_button.setObjectName('apiWorkbenchAddItemButton')
        self.add_item_button.setToolTip('新增模块或接口')
        self.add_item_button.setFixedWidth(30)
        self.add_item_menu = QMenu(self.add_item_button)
        self.add_item_menu.addAction('新增模块', self.create_archive)
        self.add_item_menu.addAction('新增接口', self.create_request)
        self.add_item_button.setMenu(self.add_item_menu)
        tree_toolbar.addWidget(self.add_item_button)
        self.batch_select_button = QPushButton()
        self.batch_select_button.setIcon(control_icon('select', self.theme))
        self.batch_select_button.setCheckable(True)
        self.batch_select_button.setToolTip('批量选择；再次点击退出')
        self.batch_select_button.setAccessibleName(self.batch_select_button.toolTip())
        self.batch_select_button.clicked.connect(self._toggle_batch_selection)
        tree_toolbar.addWidget(self.batch_select_button)
        self.remove_item_button = QPushButton()
        self.remove_item_button.setIcon(control_icon('delete', self.theme))
        self.remove_item_button.setIconSize(QSize(16, 16))
        self.remove_item_button.setAccessibleName('删除选中归档或请求')
        self.remove_item_button.setStyleSheet('QPushButton { padding: 0px; }')
        self.remove_item_button.setObjectName('apiWorkbenchRemoveItemButton')
        self.remove_item_button.setToolTip('删除选中归档或请求')
        self.remove_item_button.setFixedWidth(30)
        self.remove_item_button.clicked.connect(self.delete_tree_item)
        tree_toolbar.addWidget(self.remove_item_button)
        for button in (self.add_item_button, self.batch_select_button,
                       self.remove_item_button):
            button.setIconSize(QSize(16, 16))
            button.setFixedSize(28, 28)
            button.setStyleSheet(
                'QPushButton { padding: 0px; }'
                'QPushButton::menu-indicator { width: 0px; height: 0px; }')
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText('搜索归档、请求、方法或 URL')
        self.search_input.textChanged.connect(self._filter_tree)
        tree_layout.addWidget(self.search_input)
        self.collection_tree = QTreeWidget()
        self.collection_tree.setObjectName('apiWorkbenchCollectionTree')
        self.collection_tree.setHeaderHidden(True)
        self.collection_tree.setMinimumWidth(230)
        self.collection_tree.itemClicked.connect(self._select_tree_item)
        self.collection_tree.itemDoubleClicked.connect(self._rename_tree_item)
        self.collection_tree.itemChanged.connect(self._tree_item_changed)
        self.collection_tree.itemDelegate().closeEditor.connect(self._finish_tree_rename)
        self.collection_tree.currentItemChanged.connect(self._update_tree_action_states)
        self.collection_tree.viewport().installEventFilter(self)
        self.collection_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.collection_tree.customContextMenuRequested.connect(self._tree_menu)
        tree_layout.addWidget(self.collection_tree, 1)
        self.main_splitter.addWidget(tree_panel)

        editor_panel = QWidget()
        self.request_panel = editor_panel
        editor_layout = QVBoxLayout(editor_panel)
        editor_layout.setContentsMargins(8, 0, 0, 0)
        request_row = QHBoxLayout()
        self.request_row_layout = request_row
        self.request_method_selector = QComboBox()
        self.request_method_selector.setEditable(True)
        self.request_method_selector.addItems(
            ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'])
        self.request_method_selector.setFixedWidth(120)
        request_row.addWidget(self.request_method_selector)
        self.request_url_input = QLineEdit()
        self.request_url_input.setPlaceholderText('https://api.example.com/path')
        request_row.addWidget(self.request_url_input, 1)
        self.send_button = QPushButton('发送')
        self.send_button.clicked.connect(self.send_current_request)
        request_row.addWidget(self.send_button)
        self.save_button = QPushButton('保存')
        self.save_button.clicked.connect(self.save_current_request)
        request_row.addWidget(self.save_button)
        self.cancel_button = QPushButton('取消')
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_request)
        request_row.addWidget(self.cancel_button)
        editor_layout.addLayout(request_row)

        from notepadview.api_workbench.editors import PairEditor, AuthEditor, BodyEditor
        self.request_tabs = QTabWidget()
        self.request_editors = {
            'query': PairEditor(compact=True, title='Query 参数'),
            'headers': PairEditor(mapping=True, compact=True, title='Header 参数'),
            'cookies': PairEditor(mapping=True, compact=True, title='Cookie 参数'), 'auth': AuthEditor(),
            'body': BodyEditor(), 'variables': QPlainTextEdit(),
            'pre_script': QPlainTextEdit(), 'post_script': QPlainTextEdit(),
        }
        self.body_mode_selector = self.request_editors['body'].mode
        for key, label in [('query', 'Params'), ('body', 'Body'),
                           ('headers', 'Headers'), ('cookies', 'Cookies'),
                           ('auth', 'Auth'), ('pre_script', '前置操作'),
                           ('post_script', '后置操作'), ('variables', '设置')]:
            self.request_tabs.addTab(self.request_editors[key], label)
        self.request_tabs.setObjectName('apiRequestTabs')
        self._style_request_tabs()
        response_panel = QWidget()
        response_layout = QVBoxLayout(response_panel)
        response_layout.setContentsMargins(0, 0, 0, 0)
        self.response_status = QLabel('尚无响应')
        self.response_status.setObjectName('apiWorkbenchResponseStatus')
        response_layout.addWidget(self.response_status)
        self.response_tabs = QTabWidget()
        self.response_editors = {}
        for key, label in (
            ('pretty', 'Pretty'), ('raw', 'Raw'), ('headers', 'Headers'),
            ('cookies', 'Cookies'), ('tests', 'Tests'),
        ):
            editor = (PairEditor(mapping=True, compact=True,
                                 title='响应头' if key == 'headers' else 'Cookies')
                      if key in ('headers', 'cookies') else QPlainTextEdit())
            editor.setReadOnly(True)
            if key not in ('headers', 'cookies'):
                editor.setObjectName(f'apiWorkbenchResponse{key.title()}')
            self.response_editors[key] = editor
            self.response_tabs.addTab(editor, label)
        self.response_tabs.setObjectName('apiResponseTabs')
        self._style_request_tabs()
        response_layout.addWidget(self.response_tabs, 1)
        self.editor_splitter = QSplitter(Qt.Vertical)
        self.editor_splitter.setHandleWidth(8)
        self.editor_splitter.addWidget(self.request_tabs)
        self.editor_splitter.addWidget(response_panel)
        self.editor_splitter.setStretchFactor(0, 3)
        self.editor_splitter.setStretchFactor(1, 2)
        editor_layout.addWidget(self.editor_splitter, 1)
        self.main_splitter.addWidget(editor_panel)
        self.request_guard = QWidget(self)
        self.request_guard.setObjectName('apiRequestGuard')
        self.request_guard.setStyleSheet('QWidget#apiRequestGuard { background:transparent; border:0; }')
        self.request_guard.installEventFilter(self)
        self.request_panel.installEventFilter(self)
        self.main_splitter.setStretchFactor(0, 1)
        self.main_splitter.setStretchFactor(1, 4)
        root.addWidget(self.main_splitter, 1)
        self.setStyleSheet(
            'QTreeWidget#apiWorkbenchCollectionTree { border:1px solid palette(mid); }'
            'QLabel#apiWorkbenchResponseStatus { font-weight:600; padding:5px; }')

    def _load_data(self):
        self.collections = {item.id: item for item in self.store.list_collections()}
        self._root_collection_id = next(
            (item.id for item in self.collections.values()
             if item.source == 'notepadview:interface-root'), None)
        if self._root_collection_id is None:
            collection = ApiCollection(id=uuid4().hex, name='接口管理',
                                       source='notepadview:interface-root')
            self.store.save_collection(collection)
            self._root_collection_id = collection.id
        for collection in self.collections.values():
            if (collection.name in ('我的请求', '新归档') and not collection.source
                    and not collection.requests and not collection.operations
                    and not collection.variables and not collection.description):
                self.store.delete_collection(collection.id)
        self.environments = self.store.list_environments()
        existing_names = {item.name for item in self.environments}
        for name in ('开发环境', '测试环境', '生产环境'):
            if name not in existing_names:
                environment = Environment(id=uuid4().hex, name=name, base_url='')
                self.store.save_environment(environment)
                self.environments.append(environment)
        self._refresh_environments()
        self._refresh_tree()
        self._update_tree_action_states(None)

    def _refresh_environments(self):
        selected_id = self._selected_environment_id()
        self.environment_selector.blockSignals(True)
        self.environment_selector.clear()
        for environment in self.environments:
            self.environment_selector.addItem(environment.name, environment.id)
        index = self.environment_selector.findData(selected_id)
        if index >= 0:
            self.environment_selector.setCurrentIndex(index)
        self.environment_selector.blockSignals(False)

    def _refresh_tree(self):
        self._exit_batch_delete_mode()
        self.collections = {}
        old_root = self.collection_tree.topLevelItem(0)
        if old_root is not None:
            self.collection_tree.removeItemWidget(old_root, 0)
            QCoreApplication.removePostedEvents(self.interface_root_row, QEvent.DeferredDelete)
            self.interface_root_row.setParent(self)
        self.collection_tree.clear()
        interface_root = QTreeWidgetItem(['接口管理'])
        interface_root.setData(0, Qt.UserRole, ('collection', self._root_collection_id))
        interface_root.setFlags(interface_root.flags() & ~Qt.ItemIsUserCheckable)
        interface_root.setSizeHint(0, QSize(0, 32))
        self.collection_tree.addTopLevelItem(interface_root)
        self.collection_tree.setItemWidget(interface_root, 0, self.interface_root_row)
        self.interface_root_row.show()
        for collection in self.store.list_collections():
            self.collections[collection.id] = collection
            if collection.id == self._root_collection_id:
                root = interface_root
            else:
                root = QTreeWidgetItem([collection.name])
                root.setData(0, Qt.UserRole, ('collection', collection.id))
                interface_root.addChild(root)
            for operation in collection.operations:
                item_name = operation.summary or operation.path
                item = QTreeWidgetItem([f'{operation.method}  {item_name}'])
                item.setData(0, Qt.UserRole, ('operation', collection.id, operation))
                item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
                root.addChild(item)
            for request in collection.requests:
                item = QTreeWidgetItem([f'{request.method}  {request.name}'])
                item.setData(0, Qt.UserRole, ('request', collection.id, request))
                item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
                root.addChild(item)
            root.setExpanded(True)
        interface_root.setExpanded(True)
        history = self.store.list_history()
        if history:
            root = QTreeWidgetItem(['历史记录'])
            root.setData(0, Qt.UserRole, ('history-root',))
            self.collection_tree.addTopLevelItem(root)
            for record in history:
                request = record.request
                label = f'{request.get("method", "GET")}  {request.get("name", request.get("url", ""))}'
                item = QTreeWidgetItem([label])
                item.setData(0, Qt.UserRole, ('history', record))
                root.addChild(item)
        self._filter_tree()

    def _selected_environment_id(self):
        return self.environment_selector.currentData() if hasattr(self, 'environment_selector') else None

    def _environment_changed(self, _index):
        pass

    def _editor_state(self):
        return (self.request_method_selector.currentText(), self.request_url_input.text(),
                self.body_mode_selector.currentText(),
                tuple(editor.toPlainText() for editor in self.request_editors.values()))

    @property
    def is_dirty(self):
        return self._editor_state() != getattr(self, '_clean_state', self._editor_state())

    def _mark_clean(self):
        self._clean_state = self._editor_state()
        self.request_panel.setEnabled(self.current_request is not None)
        self._sync_request_guard()
        self._update_dirty()

    def _update_dirty(self, *_):
        self.save_button.setText('保存 *' if self.is_dirty else '保存')
        self.save_button.setToolTip('有未保存的修改' if self.is_dirty else '保存请求')

    def _select_interface_root(self):
        if self.active_worker is not None or self._batch_delete_mode:
            return
        self._select_tree_item_by_id('collection', self._root_collection_id)
        self.current_collection = self.store.get_collection(self._root_collection_id)
        self.current_request = None
        self.current_operation = None
        self._is_history_selection = False
        self._history_original_editors = None
        self._clear_request_editor()
        self._mark_clean()

    def create_archive(self):
        if self.active_worker is not None or self._batch_delete_mode:
            return
        collection = ApiCollection(id=uuid4().hex, name='未命名模块')
        try:
            self.store.save_collection(collection)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            QMessageBox.warning(self, '无法新增模块', str(error))
            return
        self.search_input.clear()
        self._refresh_tree()
        self._rename_tree_item(self._select_tree_item_by_id('collection', collection.id))

    def create_request(self):
        if self.active_worker is not None or self._batch_delete_mode:
            return
        item = self.collection_tree.currentItem()
        data = item.data(0, Qt.UserRole) if item else None
        identifier = (data[1] if data and data[0] in
                      ('collection', 'request', 'operation') else self._root_collection_id)
        collection = self.store.get_collection(identifier)
        request = RequestDefinition(id=uuid4().hex, name='未命名接口')
        collection.requests.append(request)
        try:
            self.store.save_collection(collection)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            QMessageBox.warning(self, '无法新增接口', str(error))
            return
        self.current_collection = collection
        self.current_request = request
        self.current_operation = None
        self._is_history_selection = False
        self._history_original_editors = None
        self._load_selected_request()
        self.search_input.clear()
        self._refresh_tree()
        self._rename_tree_item(self._select_tree_item_by_id('request', request.id))

    def _tree_items(self, parent=None):
        parent = parent or self.collection_tree.invisibleRootItem()
        for index in range(parent.childCount()):
            item = parent.child(index)
            yield item
            yield from self._tree_items(item)

    def _filter_tree(self, *_):
        term = self.search_input.text().strip().casefold()

        def visit(item, ancestor_match=False):
            data = item.data(0, Qt.UserRole)
            extra = ''
            if data and data[0] in ('request', 'operation'):
                extra = getattr(data[2], 'url', getattr(data[2], 'path', ''))
            match = ancestor_match or term in (item.text(0) + ' ' + extra).casefold()
            visible = match
            for index in range(item.childCount()):
                visible = visit(item.child(index), match) or visible
            if data and data[0] == 'collection' and data[1] == self._root_collection_id:
                visible = True
            item.setHidden(not visible)
            if term and visible:
                item.setExpanded(True)
            return visible

        for index in range(self.collection_tree.topLevelItemCount()):
            visit(self.collection_tree.topLevelItem(index))

    def _tree_menu(self, point):
        item = self.collection_tree.itemAt(point)
        if item is None:
            return
        self.collection_tree.setCurrentItem(item)
        data = item.data(0, Qt.UserRole)
        if (data[0] not in ('collection', 'request', 'operation')
                or data[0] == 'collection' and data[1] == self._root_collection_id):
            return
        menu = QMenu(self)
        menu.addAction('重命名…', lambda: self._rename_tree_item(item))
        menu.addAction('删除…', self.delete_tree_item)
        if data[0] != 'collection':
            menu.addAction('批量删除…', lambda: self._start_batch(item))
        menu.exec(self.collection_tree.viewport().mapToGlobal(point))

    def _start_batch(self, item):
        self._long_press_item = item
        self._enter_batch_delete_mode()
    def _select_tree_item_by_id(self, item_type, item_id):
        for item in self._tree_items():
            data = item.data(0, Qt.UserRole)
            if not data or data[0] != item_type:
                continue
            identifier = data[2].id if item_type in ('request', 'operation') else data[1]
            if identifier == item_id:
                self.collection_tree.setCurrentItem(item)
                return item
        return None

    def _update_tree_action_states(self, current, _previous=None):
        self.batch_select_button.setEnabled(self.active_worker is None)
        if self.active_worker is not None:
            self.add_item_button.setEnabled(False)
            self.remove_item_button.setEnabled(False)
            return
        self.add_item_button.setEnabled(not self._batch_delete_mode)
        if getattr(self, '_batch_delete_mode', False):
            self.remove_item_button.setEnabled(True)
            return
        data = current.data(0, Qt.UserRole) if current else None
        self.remove_item_button.setEnabled(
            bool(data and data[0] in ('collection', 'request', 'operation')
                 and not (data[0] == 'collection' and data[1] == self._root_collection_id)))

    def _sync_request_guard(self):
        panel = self.request_panel
        self.request_guard.setGeometry(
            panel.mapTo(self, panel.rect().topLeft()).x(),
            panel.mapTo(self, panel.rect().topLeft()).y(),
            panel.width(), panel.height())
        self.request_guard.setVisible(self.current_request is None)
        self.request_guard.raise_()

    def eventFilter(self, watched, event):
        if watched is getattr(self, 'request_panel', None) and event.type() in (
                QEvent.Resize, QEvent.Move, QEvent.Show):
            if hasattr(self, 'request_guard'):
                self._sync_request_guard()
        if watched is getattr(self, 'request_guard', None):
            if event.type() == QEvent.MouseButtonPress:
                QToolTip.showText(event.globalPosition().toPoint(), '请在接口下使用', self)
                return True
        tree = getattr(self, 'collection_tree', None)
        if tree is not None and watched is tree.viewport():
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                position = event.position().toPoint()
                item = self.collection_tree.itemAt(position)
                data = item.data(0, Qt.UserRole) if item else None
                if (not self._batch_delete_mode and data
                        and data[0] in ('request', 'operation')):
                    self._long_press_item = item
                    self._long_press_activated = False
                    self._long_press_timer.start()
            elif event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                self._long_press_timer.stop()
                if self._long_press_activated:
                    self._long_press_activated = False
                    self._long_press_item = None
                    return True
                self._long_press_item = None
        return super().eventFilter(watched, event)

    def _toggle_batch_selection(self):
        if self._batch_delete_mode:
            self._exit_batch_delete_mode()
        else:
            self._enter_batch_delete_mode()

    def _enter_batch_delete_mode(self):
        if self.active_worker is not None:
            return
        self._batch_delete_mode = True
        self.batch_select_button.setChecked(True)
        self._long_press_activated = self._long_press_item is not None
        for item in self._tree_items():
            data = item.data(0, Qt.UserRole)
            if data and data[0] in ('request', 'operation'):
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(0, Qt.Unchecked)
        self._update_tree_action_states(self.collection_tree.currentItem())

    def _exit_batch_delete_mode(self):
        self._long_press_timer.stop()
        self._batch_delete_mode = False
        self._long_press_activated = False
        self._long_press_item = None
        self.batch_select_button.setChecked(False)
        for item in self._tree_items():
            item.setData(0, Qt.CheckStateRole, None)
            item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
        self._update_tree_action_states(self.collection_tree.currentItem())

    def _delete_checked_tree_items(self):
        selected = []
        for item in self._tree_items():
            data = item.data(0, Qt.UserRole)
            if (data and data[0] in ('request', 'operation')
                    and item.checkState(0) == Qt.Checked):
                selected.append((data[1], data[0], data[2].id))
        if not selected:
            QMessageBox.information(self, '删除请求', '请先勾选要删除的请求')
            return
        if QMessageBox.question(self, '批量删除', f'确定删除选中的 {len(selected)} 个请求吗？',
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        selected_ids = {(kind, item_id) for _collection_id, kind, item_id in selected}
        try:
            self.store.delete_collection_items(selected)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            QMessageBox.warning(self, '无法删除请求', str(error))
            return
        self.collections = {
            collection.id: collection for collection in self.store.list_collections()
        }
        if (self.current_request is not None
                and ('request', self.current_request.id) in selected_ids):
            self.current_request = None
            self.current_operation = None
            self._history_original_editors = None
            self._clear_request_editor()
            self._mark_clean()
        elif (self.current_operation is not None
                and ('operation', self.current_operation.id) in selected_ids):
            self.current_request = None
            self.current_operation = None
            self._history_original_editors = None
            self._clear_request_editor()
            self._mark_clean()
        self._exit_batch_delete_mode()
        self._refresh_tree()
        self.collection_tree.clearSelection()
        self.collection_tree.setCurrentItem(None)
        self._update_tree_action_states(None)

    def _clear_request_editor(self):
        self.request_method_selector.setCurrentText('GET')
        self.request_url_input.clear()
        for key, editor in self.request_editors.items():
            editor.setPlainText('[]' if key == 'query' else '{}'
                                if key in ('headers', 'auth', 'cookies', 'variables')
                                else '')
        self.body_mode_selector.setCurrentText('none')

    def delete_tree_item(self):
        if getattr(self, '_batch_delete_mode', False):
            self._delete_checked_tree_items()
            return
        item = self.collection_tree.currentItem()
        data = item.data(0, Qt.UserRole) if item else None
        if not data:
            return
        if data[0] == 'collection':
            collection = self.collections.get(data[1])
            if collection is None or collection.id == self._root_collection_id:
                return
            answer = QMessageBox.question(
                self,
                '删除归档',
                f'确定删除归档“{collection.name}”及其中的所有请求吗？',
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            try:
                self.store.delete_collection(collection.id)
            except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
                QMessageBox.warning(self, '无法删除归档', str(error))
                return
            self.collections.pop(collection.id, None)
            if (self.current_collection is not None
                    and self.current_collection.id == collection.id):
                self.current_collection = None
                self.current_request = None
                self.current_operation = None
                self._is_history_selection = False
                self._history_original_editors = None
                self._clear_request_editor()
        elif data[0] in ('request', 'operation'):
            if QMessageBox.question(
                    self, '删除请求', '确定删除所选请求吗？',
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            collection = self.collections.get(data[1])
            if collection is None:
                return
            if data[0] == 'request':
                collection.requests = [
                    request for request in collection.requests
                    if request.id != data[2].id]
                removes_current = (
                    self.current_request is not None
                    and self.current_request.id == data[2].id)
            else:
                collection.operations = [
                    operation for operation in collection.operations
                    if operation.id != data[2].id]
                removes_current = (
                    self.current_operation is not None
                    and self.current_operation.id == data[2].id)
            try:
                self.store.save_collection(collection)
            except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
                QMessageBox.warning(self, '无法删除请求', str(error))
                return
            self.collections[collection.id] = collection
            if removes_current:
                self.current_collection = collection
                self.current_request = None
                self.current_operation = None
                self._history_original_editors = None
                self._clear_request_editor()
        elif data[0] == 'history':
            QMessageBox.information(self, '无法删除历史记录', '历史记录不能通过此按钮删除')
            return
        else:
            return
        if self.current_request is None:
            self._mark_clean()
        self._refresh_tree()
        self.collection_tree.clearSelection()
        self.collection_tree.setCurrentItem(None)
        self._update_tree_action_states(None)

    def _rename_tree_item(self, item, _column=0):
        data = item.data(0, Qt.UserRole) if item else None
        if (self._batch_delete_mode or self.active_worker is not None or not data
                or data[0] not in ('collection', 'request', 'operation')
                or data[0] == 'collection' and data[1] == self._root_collection_id):
            return
        target = self.collections.get(data[1]) if data[0] == 'collection' else data[2]
        name = target.name if data[0] != 'operation' else target.summary or target.path
        self.collection_tree.blockSignals(True)
        item.setData(0, Qt.UserRole + 1, name)
        item.setText(0, name)
        item.setFlags(item.flags() | Qt.ItemIsEditable)
        self.collection_tree.blockSignals(False)
        self.collection_tree.editItem(item, 0)

    def _finish_tree_rename(self, *_):
        self.collection_tree.blockSignals(True)
        for item in self._tree_items():
            name = item.data(0, Qt.UserRole + 1)
            if name is None:
                continue
            data = item.data(0, Qt.UserRole)
            item.setData(0, Qt.UserRole + 1, None)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            item.setText(0, name if data[0] == 'collection' else f'{data[2].method}  {name}')
        self.collection_tree.blockSignals(False)

    def _tree_item_changed(self, item, column):
        original = item.data(0, Qt.UserRole + 1)
        data = item.data(0, Qt.UserRole)
        if (original is None or column != 0
                or data and data[0] == 'collection' and data[1] == self._root_collection_id):
            return
        data = item.data(0, Qt.UserRole)
        name = item.text(0).strip() or original
        collection = self.store.get_collection(data[1])
        if collection is None:
            return
        target = collection if data[0] == 'collection' else next(
            entry for entry in (collection.requests if data[0] == 'request'
                                else collection.operations) if entry.id == data[2].id)
        if data[0] == 'operation':
            target.summary = name
        else:
            target.name = name
        try:
            self.store.save_collection(collection)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            QMessageBox.warning(self, '无法重命名', str(error))
            name = original
        else:
            self.collections[collection.id] = collection
            if self.current_collection is not None and self.current_collection.id == collection.id:
                self.current_collection = collection
            if data[0] == 'request' and self.current_request is not None and self.current_request.id == target.id:
                self.current_request.name = name
            if data[0] == 'operation' and self.current_operation is not None and self.current_operation.id == target.id:
                self.current_operation.summary = name
                self.current_request.name = name
        self.collection_tree.blockSignals(True)
        item.setData(0, Qt.UserRole + 1, None)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        if data[0] != 'collection':
            item.setData(0, Qt.UserRole, (data[0], data[1], target))
        item.setText(0, name if data[0] == 'collection' else f'{target.method}  {name}')
        self.collection_tree.blockSignals(False)

    def _select_tree_item(self, item, _column=0):
        if item is None or self._batch_delete_mode:
            return
        data = item.data(0, Qt.UserRole)
        if not data or data[0] == 'history-root':
            return
        if data[0] == 'collection':
            self._clear_request_editor()
            self._is_history_selection = False
            self._history_original_editors = None
            self.current_collection = self.collections.get(data[1])
            self.current_request = None
            self.current_operation = None
        elif data[0] == 'operation':
            self._is_history_selection = False
            self._history_original_editors = None
            self.current_collection = self.collections.get(data[1])
            self.current_operation = data[2]
            operation = self.current_operation
            self.current_request = RequestDefinition(
                id=uuid4().hex,
                name=operation.summary or f'{operation.method} {operation.path}',
                method=operation.method,
                url=operation.path,
                query=[
                    {'key': item.get('name', ''), 'value': ''}
                    for item in operation.parameters if item.get('in') == 'query'
                ],
                headers={
                    item.get('name', ''): ''
                    for item in operation.parameters if item.get('in') == 'header'
                    and item.get('name')
                },
                folder='',
            )
        elif data[0] == 'request':
            self._is_history_selection = False
            self._history_original_editors = None
            self.current_collection = self.collections.get(data[1])
            self.current_request = data[2]
            self.current_operation = None
        elif data[0] == 'history':
            self._is_history_selection = True
            self.current_collection = None
            self.current_request = RequestDefinition.from_dict(data[1].request)
            self.current_operation = None
            self._show_history_response(data[1])
        self._load_selected_request()
        self._mark_clean()

    def _show_history_response(self, history):
        response = history.response
        status = response.get('status_code')
        self.response_status.setText(
            f'{status} {response.get("status", "")} · '
            f'{response.get("elapsed_ms", 0):.1f} ms · {response.get("size_bytes", 0)} bytes')
        body = response.get('body', '')
        try:
            pretty = json.dumps(json.loads(body), ensure_ascii=False, indent=2)
        except (json.JSONDecodeError, TypeError):
            pretty = body
        self.response_editors['pretty'].setPlainText(pretty)
        self.response_editors['raw'].setPlainText(body)
        headers = response.get('headers', {})
        masked_headers = {
            name: '[已隐藏]' if is_sensitive_header(name) else value
            for name, value in headers.items()
        }
        self.response_editors['headers'].setPlainText(
            json.dumps(masked_headers, ensure_ascii=False, indent=2))
        self.response_editors['cookies'].setPlainText(
            json.dumps({name: '[已隐藏]' for name in response.get('cookies', {})},
                       ensure_ascii=False, indent=2))
        self.response_editors['tests'].setPlainText('\n'.join(response.get('tests', [])))
        self.response_tabs.setCurrentIndex(0)

    def _load_selected_request(self):
        request = self.current_request
        if request is None:
            return
        self.request_method_selector.setCurrentText(request.method)
        self.request_url_input.setText(request.url)
        self.request_editors['query'].setPlainText(json.dumps(request.query, ensure_ascii=False, indent=2))
        values = {
            'headers': dict(request.headers),
            'auth': dict(request.auth),
            'cookies': dict(request.cookies),
            'variables': {
                name: dict(value) for name, value in request.variables.items()
            },
        }
        if self._is_history_selection:
            values['headers'] = {
                name: '[已隐藏]' if is_sensitive_header(name) else value
                for name, value in values['headers'].items()
            }
            values['auth'] = {
                name: '[已隐藏]' if name.casefold() in (
                    'token', 'password', 'secret', 'value',
                ) else value
                for name, value in values['auth'].items()
            }
            values['cookies'] = {
                name: '[已隐藏]' for name in values['cookies']
            }
            values['variables'] = {
                name: ({**value, 'value': '[已隐藏]'}
                       if value.get('secret') else value)
                for name, value in values['variables'].items()
            }
            keys = ('headers', 'auth', 'cookies', 'variables')
            expected_text = {
                key: json.dumps(values[key], ensure_ascii=False, indent=2)
                for key in keys
            }
            self._history_original_editors = {
                'values': {
                    'headers': dict(request.headers),
                    'auth': dict(request.auth),
                    'cookies': dict(request.cookies),
                    'variables': {
                        name: dict(value)
                        for name, value in request.variables.items()
                    },
                },
                'expected_text': expected_text,
            }
        for key in ('headers', 'auth', 'cookies', 'variables'):
            self.request_editors[key].setPlainText(
                json.dumps(values[key], ensure_ascii=False, indent=2))
        self.body_mode_selector.setCurrentText(request.body_mode)
        self.request_editors['body'].setPlainText(request.body)
        self.request_editors['pre_script'].setPlainText(request.pre_script)
        self.request_editors['post_script'].setPlainText(request.post_script)
        if self._history_original_editors is not None:
            self._history_original_editors['expected_text'] = {
                key: self.request_editors[key].toPlainText()
                for key in ('headers', 'auth', 'cookies', 'variables')}
        self._mark_clean()

    @staticmethod
    def _parse_editor_json(editor, expected, label):
        try:
            value = json.loads(editor.toPlainText() or ('[]' if expected is list else '{}'))
        except json.JSONDecodeError as error:
            raise ValueError(f'{label} JSON 格式错误：{error}') from error
        if not isinstance(value, expected):
            raise ValueError(f'{label} 必须是 JSON {expected.__name__}')
        return value

    def _request_from_editor(self):
        if self.current_request is None:
            request_id = uuid4().hex
            name = '新请求'
            folder = ''
        else:
            request_id = self.current_request.id
            name = self.current_request.name
            folder = self.current_request.folder
        variables = self._parse_editor_json(
            self.request_editors['variables'], dict, '变量')
        cookies = self._parse_editor_json(
            self.request_editors['cookies'], dict, 'Cookies')
        headers = self._parse_editor_json(
            self.request_editors['headers'], dict, 'Headers')
        auth = self._parse_editor_json(
            self.request_editors['auth'], dict, '认证')
        if self._history_original_editors is not None:
            originals = self._history_original_editors['values']
            expected = self._history_original_editors['expected_text']
            for key, value in (
                ('headers', headers), ('auth', auth),
                ('cookies', cookies), ('variables', variables),
            ):
                if self.request_editors[key].toPlainText() == expected[key]:
                    if key == 'headers':
                        headers = dict(originals[key])
                    elif key == 'auth':
                        auth = dict(originals[key])
                    elif key == 'cookies':
                        cookies = dict(originals[key])
                    else:
                        variables = {
                            name: dict(item) for name, item in originals[key].items()
                        }
        return RequestDefinition(
            id=request_id,
            name=name,
            method=self.request_method_selector.currentText().strip().upper(),
            url=self.request_url_input.text().strip(),
            query=self._parse_editor_json(self.request_editors['query'], list, 'Params'),
            headers=headers,
            auth=auth,
            body_mode=self.body_mode_selector.currentText(),
            body=self.request_editors['body'].toPlainText(),
            pre_script=self.request_editors['pre_script'].toPlainText(),
            post_script=self.request_editors['post_script'].toPlainText(),
            variables=variables,
            folder=folder,
            cookies=cookies,
        )

    def _selected_environment(self):
        identifier = self._selected_environment_id()
        return next((item for item in self.environments if item.id == identifier), None)

    def _ensure_collection(self):
        identifier = (self.current_collection.id if self.current_collection is not None
                      else self._root_collection_id)
        self.current_collection = self.store.get_collection(identifier)
        return self.current_collection

    def save_current_request(self):
        if self.current_request is None:
            return
        try:
            request = self._request_from_editor()
            collection = self._ensure_collection()
            collection.requests = [
                item for item in collection.requests if item.id != request.id]
            collection.requests.append(request)
            self.store.save_collection(collection)
        except (ValueError, TypeError, OSError, RuntimeError, sqlite3.Error) as error:
            QMessageBox.warning(self, '无法保存请求', str(error))
            return
        self.collections[collection.id] = collection
        self.current_collection = collection
        self.current_request = request
        self.current_operation = None
        self._is_history_selection = False
        self._refresh_tree()
        self._select_tree_item_by_id('request', request.id)
        self._mark_clean()
        return True

    def send_current_request(self):
        if self.current_request is None:
            return
        try:
            request = self._request_from_editor()
            environment = self._selected_environment()
            if environment is None:
                raise ValueError('请先创建并选择一个环境')
            collection = self.current_collection
            token = CancelToken()
            worker = _ApiExecutionWorker(
                request, environment, collection.variables if collection else {},
                self.store.get_cookies(environment.id), token)
            worker.signals.finished.connect(self._request_finished)
            self.cancel_token = token
            self.active_worker = worker
            self.batch_select_button.setEnabled(False)
            self.collection_tree.setEnabled(False)
            self.add_item_button.setEnabled(False)
            self.remove_item_button.setEnabled(False)
            self.send_button.setEnabled(False)
            self.cancel_button.setEnabled(True)
            self.response_status.setText('请求中…')
            self.thread_pool.start(worker)
        except (ValueError, TypeError, OSError, RuntimeError, sqlite3.Error) as error:
            QMessageBox.warning(self, '无法发送请求', str(error))

    def cancel_request(self):
        if self.cancel_token is not None:
            self.cancel_token.cancel()
            self.cancel_button.setEnabled(False)
            self.response_status.setText('正在取消…')

    def _request_finished(self, output):
        self.active_worker = None
        self.cancel_token = None
        self.collection_tree.setEnabled(True)
        self._update_tree_action_states(self.collection_tree.currentItem())
        self.send_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        if output.error:
            self.response_status.setText(f'请求准备失败：{output.error}')
            self.response_editors['tests'].setPlainText(output.error)
            return
        try:
            if output.cookies is not None:
                self.store.save_cookies(output.environment.id, output.cookies)
            if output.environment != self._selected_environment():
                self.store.save_environment(output.environment)
                self.environments = self.store.list_environments()
                self._refresh_environments()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            self.response_status.setText(f'无法保存请求数据：{error}')
            return
        script_tests = []
        for label, result in (('Pre-request', output.pre_script), ('Tests', output.post_script)):
            if result is None:
                continue
            if result.error:
                script_tests.append(f'{label}: {result.error}')
            for test in result.tests:
                script_tests.append(
                    f'{"PASS" if test.passed else "FAIL"} {test.name}'
                    + (f': {test.message}' if test.message else ''))
        result = output.result
        if result is None:
            self.response_status.setText('请求未返回结果')
            return
        if result.transport_error is not None:
            self.response_status.setText(
                f'请求失败（{result.transport_error.kind}）：{result.transport_error.message}')
            self.response_editors['tests'].setPlainText('\n'.join(script_tests))
            return
        response = result.response
        self.response_status.setText(
            f'{response.status_code} {response.status} · {response.elapsed_ms:.1f} ms · '
            f'{response.size_bytes} bytes')
        try:
            parsed = json.loads(response.body)
            pretty = json.dumps(parsed, ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            pretty = response.body
        self.response_editors['pretty'].setPlainText(pretty)
        self.response_editors['raw'].setPlainText(response.body)
        masked_headers = {
            name: '[已隐藏]' if is_sensitive_header(name) else value
            for name, value in response.headers.items()
        }
        masked_cookies = {name: '[已隐藏]' for name in response.cookies}
        self.response_editors['headers'].setPlainText(
            json.dumps(masked_headers, ensure_ascii=False, indent=2))
        self.response_editors['cookies'].setPlainText(
            json.dumps(masked_cookies, ensure_ascii=False, indent=2))
        self.response_editors['tests'].setPlainText('\n'.join(script_tests))
        request_data = output.request.to_dict()
        history = RequestHistory(
            id=uuid4().hex,
            completed_at=datetime.now(timezone.utc).isoformat(),
            request=request_data,
            response={
                'status_code': response.status_code,
                'status': response.status,
                'headers': response.headers,
                'cookies': response.cookies,
                'body': response.body,
                'elapsed_ms': response.elapsed_ms,
                'size_bytes': response.size_bytes,
                'tests': script_tests,
            },
        )
        self.store.save_history(history)
        self._refresh_tree()

    def create_environment(self):
        self._show_environment_form(
            Environment(id=uuid4().hex, name='新环境', base_url=''), creating=True)

    def edit_environment(self):
        environment = self._selected_environment()
        if environment is None:
            QMessageBox.information(self, '编辑环境', '请先创建并选择一个环境')
            return
        self._show_environment_form(environment)

    def _show_environment_form(self, environment, *, creating=False):
        dialog = QDialog(self)
        dialog.setWindowTitle('新建环境' if creating else '编辑环境')
        dialog.setMinimumSize(640, 560)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('环境名称'))
        name_input = QLineEdit(environment.name)
        layout.addWidget(name_input)
        layout.addWidget(QLabel('Base URL'))
        base_url_input = QLineEdit(environment.base_url)
        layout.addWidget(base_url_input)
        from notepadview.api_workbench.editors import PairEditor
        headers_editor = PairEditor(mapping=True, compact=True, title='默认请求头')
        headers_editor.setStyleSheet(self.request_tabs.styleSheet())
        headers_editor.setPlainText(json.dumps(
            environment.default_headers, ensure_ascii=False, indent=2))
        layout.addWidget(headers_editor)
        layout.addWidget(QLabel('变量（JSON 对象，例如 {"token":{"value":"...","secret":true}}）'))
        variables_editor = QPlainTextEdit()
        variables_editor.setPlainText(json.dumps(
            environment.variables, ensure_ascii=False, indent=2))
        layout.addWidget(variables_editor)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel, parent=dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            headers = json.loads(headers_editor.toPlainText() or '{}')
            variables = json.loads(variables_editor.toPlainText() or '{}')
            if not isinstance(headers, dict) or not isinstance(variables, dict):
                raise ValueError('请求头和变量必须是 JSON 对象')
            updated = Environment(
                id=environment.id,
                name=name_input.text().strip(),
                base_url=base_url_input.text().strip(),
                default_headers=headers,
                variables=variables,
            )
            self.store.save_environment(updated)
        except (json.JSONDecodeError, ValueError, TypeError, RuntimeError, sqlite3.Error) as error:
            QMessageBox.warning(self, '环境无效', str(error))
            return
        self.environments = self.store.list_environments()
        self._refresh_environments()
        self.environment_selector.setCurrentIndex(
            self.environment_selector.findData(environment.id))

    def import_openapi_file(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, '导入 Swagger / OpenAPI', '', 'OpenAPI (*.json *.yaml *.yml);;所有文件 (*)')
        if not filename:
            return
        try:
            with open(filename, 'rb') as source:
                result = import_openapi(source.read(), source_name=filename)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            QMessageBox.warning(self, '导入失败', str(error))
            return
        self._save_openapi_result(result)

    def choose_openapi_import(self):
        menu = QMenu(self)
        local_action = menu.addAction('从本地文件导入…')
        url_action = menu.addAction('从 URL 导入…')
        paste_action = menu.addAction('粘贴 JSON / YAML…')
        selected = menu.exec(self.import_openapi_button.mapToGlobal(
            self.import_openapi_button.rect().bottomLeft()))
        if selected is local_action:
            self.import_openapi_file()
        elif selected is url_action:
            self.import_openapi_url()
        elif selected is paste_action:
            self.import_openapi_text()

    def import_openapi_url(self):
        url, accepted = QInputDialog.getText(self, '从 URL 导入 OpenAPI', '文档 URL')
        if not accepted or not url.strip():
            return
        worker = _OpenApiFetchWorker(url.strip())
        worker.signals.finished.connect(self._openapi_fetch_finished)
        self._openapi_worker = worker
        self.response_status.setText('正在获取 OpenAPI 文档…')
        self.thread_pool.start(worker)

    def _openapi_fetch_finished(self, result, error):
        self._openapi_worker = None
        if error:
            QMessageBox.warning(self, '导入失败', error)
            return
        self._save_openapi_result(result)

    def import_openapi_text(self):
        text, accepted = QInputDialog.getMultiLineText(
            self, '粘贴 OpenAPI 文档', 'Swagger / OpenAPI JSON 或 YAML')
        if not accepted or not text.strip():
            return
        try:
            result = import_openapi(text, source_name='pasted OpenAPI document')
        except ValueError as error:
            QMessageBox.warning(self, '导入失败', str(error))
            return
        self._save_openapi_result(result)

    def _save_openapi_result(self, result):
        try:
            self.store.save_collection(result.collection)
            self.collections[result.collection.id] = result.collection
            if result.servers and not self.environments:
                environment = Environment(
                    id=uuid4().hex,
                    name='Imported',
                    base_url=result.servers[0],
                )
                self.store.save_environment(environment)
                self.environments = self.store.list_environments()
                self._refresh_environments()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            QMessageBox.warning(self, '导入失败', str(error))
            return
        self._refresh_tree()

    def _style_request_tabs(self):
        colors = THEMES[self.theme]
        self.collection_tree.setStyleSheet(
            f'QTreeWidget#apiWorkbenchCollectionTree {{ background:{colors["bg"]}; '
            f'color:{colors["fg"]}; border:1px solid {colors["border"]}; outline:0; '
            f'selection-background-color:{colors["selection"]}; selection-color:{colors["fg"]}; }}'
            f'QTreeWidget#apiWorkbenchCollectionTree::item:hover {{ background:{colors["hover"]}; }}'
            'QTreeWidget#apiWorkbenchCollectionTree::item:selected,'
            'QTreeWidget#apiWorkbenchCollectionTree::item:selected:!active {'
            f'background:{colors["selection"]}; color:{colors["fg"]}; }}')
        self.request_tabs.setStyleSheet(
            f'QTabWidget#apiRequestTabs::pane {{ border:0; border-top:1px solid {colors["border"]}; }}'
            f'QTabWidget#apiRequestTabs QTabBar::tab {{ background:transparent; color:{colors["muted"]}; '
            'padding:9px 10px; border:0; border-bottom:2px solid transparent; }'
            'QTabWidget#apiRequestTabs QTabBar::tab:selected { color:#8b5cf6; border-bottom:2px solid #8b5cf6; }'
            f'QTabBar#apiBodyTypes::tab {{ background:transparent; color:{colors["fg"]}; '
            'padding:4px 10px; margin:6px 3px; border:0; border-radius:11px; }'
            f'QTabBar#apiBodyTypes::tab:selected {{ background:{colors["accent"]}; color:{colors["bg"]}; }}'
            f'QWidget#apiCompactPairEditor QTableWidget QComboBox {{ min-height:0; min-width:0; '
            'padding:0 22px 0 6px; margin:2px; }'
            'QWidget#apiCompactPairEditor QTableWidget QPushButton { min-height:0; '
            'padding:1px 8px; margin:2px; text-align:left; }'
            f'QWidget#apiCompactPairEditor {{ background:{colors["bg"]}; }}'
            f'QWidget#apiCompactPairEditor QTableWidget {{ background:{colors["bg"]}; '
            f'color:{colors["fg"]}; border:0; selection-background-color:{colors["selection"]}; '
            f'selection-color:{colors["fg"]}; }}'
            f'QWidget#apiCompactPairEditor QTableWidget::item:selected {{ '
            f'background:{colors["selection"]}; color:{colors["fg"]}; }}'
            f'QWidget#apiCompactPairEditor QTableWidget QLineEdit {{ min-height:0; '
            f'padding:0; margin:0; border:1px solid {colors["accent"]}; border-radius:2px; '
            f'background:{colors["bg"]}; color:{colors["fg"]}; '
            f'selection-background-color:{colors["selection"]}; selection-color:{colors["fg"]}; }}'
            f'QWidget#apiCompactPairEditor QHeaderView::section {{ background:{colors["bg"]}; '
            f'color:{colors["muted"]}; border:0; border-bottom:1px solid {colors["border"]}; '
            'padding:6px 10px; }'
            f'QWidget#apiCompactPairEditor QTableWidget::item {{ padding:4px 10px; '
            f'border-bottom:1px solid {colors["border"]}; }}')

        if hasattr(self, 'response_tabs'):
            self.response_tabs.setStyleSheet(
                self.request_tabs.styleSheet().replace('apiRequestTabs', 'apiResponseTabs') +
                f'QTabWidget#apiResponseTabs QPlainTextEdit {{ border:0; padding:10px; '
                f'background:{colors["bg"]}; color:{colors["fg"]}; }}')

    def set_theme(self, theme):
        self.theme = theme if theme in THEMES else 'nv'
        self.add_item_button.setIcon(control_icon('add', self.theme))
        self.remove_item_button.setIcon(control_icon('delete', self.theme))
        self.batch_select_button.setIcon(control_icon('select', self.theme))
        self._style_request_tabs()
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def _return_to_documents(self):
        if self.on_return is not None:
            self.on_return()

    def shutdown(self):
        if self.cancel_token is not None:
            self.cancel_token.cancel()
        self.thread_pool.waitForDone(1000)
