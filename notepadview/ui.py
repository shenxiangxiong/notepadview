from pathlib import Path

from PySide6.QtCore import QEvent, QRectF, QSize, Qt
from PySide6.QtGui import QActionGroup, QPainterPath, QPixmap, QRegion
from PySide6.QtWidgets import (
    QCheckBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QMessageBox, QPushButton, QSizePolicy, QSplitter, QStackedWidget, QStyle,
    QTabBar, QToolBar, QToolButton, QVBoxLayout, QWidget,
)

from notepadview.theme import ThemedMenu as QMenu
from PySide6.QtWebEngineCore import QWebEngineSettings

from notepadview.editor import SourceEditor
from notepadview.file_tree import FileTree
from notepadview.preview import PreviewView


def scroll_button(parent, symbol, tooltip, callback):
    button = QToolButton(parent)
    button.setObjectName('scrollButton')
    button.setText(symbol)
    button.setToolButtonStyle(Qt.ToolButtonTextOnly)
    button.setFixedSize(30, 30)
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    button.clicked.connect(callback)
    return button


class WorkspaceContainer(QWidget):
    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 父控件遮罩同时裁剪矩形子控件，只保留整个工作区的外侧圆角。
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 2, 2)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))


class FloatingScrollControls(QWidget):
    def __init__(self, panel, top_callback, bottom_callback):
        super().__init__(panel)
        self.panel = panel
        self.setObjectName('scrollControls')
        self.setFixedSize(36, 72)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.top_button = scroll_button(self, '⤒', '到顶', top_callback)
        self.bottom_button = scroll_button(self, '⤓', '到底', bottom_callback)
        layout.addWidget(self.top_button)
        layout.addWidget(self.bottom_button)
        panel.installEventFilter(self)
        self.move_to_corner()

    def move_to_corner(self):
        footer = 23
        self.move(max(8, self.panel.width() - self.width() - 14),
                  max(8, self.panel.height() - footer - self.height() - 10))

    def eventFilter(self, watched, event):
        if watched is self.panel and event.type() in (QEvent.Resize, QEvent.Show):
            self.move_to_corner()
        return super().eventFilter(watched, event)


def build_actions(w):
    a = w.action
    w.new_action = a('文件…', w.choose_new_document, 'Ctrl+N', QStyle.SP_FileIcon)
    w.new_category_action = a('文件分类…', lambda: w.file_list.request_category())
    w.open_action = a('打开…', w.open_document, 'Ctrl+O', QStyle.SP_DialogOpenButton)
    w.save_action = a('保存', w.save_document, 'Ctrl+S', QStyle.SP_DialogSaveButton)
    w.save_as_action = a('另存为…', w.save_as, 'Ctrl+Shift+S')
    w.rename_action = a('重命名…', lambda: w.request_rename_document(), 'F2')
    w.rename_action.setEnabled(False)
    w.new_plain_text_action = a('新建纯文本', lambda: w.new_document('txt'), 'Ctrl+Alt+N')
    w.export_action = a('导出 HTML…', w.export_html, 'Ctrl+Shift+E', QStyle.SP_ArrowUp)
    menu_bar = w.menuBar()
    menu_bar.setNativeMenuBar(False)
    w.brand = QLabel('Notepad View')
    w.brand.setObjectName('brand')
    brand_group = QWidget()
    w.brand_group = brand_group
    brand_group.setObjectName('brandGroup')
    brand_group.setFixedHeight(menu_bar.minimumHeight())
    brand_layout = QHBoxLayout(brand_group)
    brand_layout.setContentsMargins(8, 0, 18, 5)
    brand_layout.setSpacing(8)
    brand_layout.setAlignment(Qt.AlignVCenter)
    w.brand_icon = QLabel()
    w.brand_icon.setObjectName('brandIcon')
    w.brand_icon.setFixedSize(20, 20)
    brand_pixmap = QPixmap(str(Path(__file__).resolve().parent / 'assets' / 'app.png')).scaled(
        40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    brand_pixmap.setDevicePixelRatio(2)
    w.brand_icon.setPixmap(brand_pixmap)
    w.brand_icon.setAlignment(Qt.AlignCenter)
    brand_layout.addWidget(w.brand_icon)
    brand_layout.addWidget(w.brand)
    menu_bar.setCornerWidget(brand_group, Qt.TopLeftCorner)
    menu = menu_bar.addMenu('文件')
    w.new_menu = menu.addMenu('新建')
    w.new_menu.addActions([w.new_action, w.new_category_action])
    w.addAction(w.new_action)
    menu.addActions([w.open_action, w.save_action, w.save_as_action])
    menu.addAction(w.rename_action)
    menu.addAction(w.new_plain_text_action)
    menu.addSeparator()
    menu.addAction(w.export_action)
    menu.addAction(a('新建思维导图', lambda: w.new_diagram('mindmap')))
    menu.addAction(a('新建流程图', lambda: w.new_diagram('flowchart')))
    w.recent_menu = menu.addMenu('最近打开')
    menu.addSeparator()
    menu.addAction(a('退出', w.close, 'Alt+F4'))
    edit = menu_bar.addMenu('编辑')
    w.undo_action = a('撤销', w.undo_current, 'Ctrl+Z', QStyle.SP_ArrowBack)
    w.redo_action = a('重做', w.redo_current, 'Ctrl+Y', QStyle.SP_ArrowForward)
    edit.addActions([w.undo_action, w.redo_action])
    edit.addSeparator()
    edit.addAction(a('剪切', lambda: w.edit_current('cut'), 'Ctrl+X'))
    w.copy_action = a('复制', w.copy_current, 'Ctrl+C')
    edit.addAction(w.copy_action)
    edit.addAction(a('粘贴', lambda: w.edit_current('paste'), 'Ctrl+V'))
    edit.addAction(a('全选', lambda: w.edit_current('selectAll'), 'Ctrl+A'))
    edit.addSeparator()
    w.find_action = a('查找与替换', w.toggle_find, 'Ctrl+F')
    w.find_action.setCheckable(True)
    edit.addAction(w.find_action)
    edit.addAction(a('查找下一个', w.find_next, 'F3'))
    w.format_document_action = a('格式化文档', w.format_document, 'Ctrl+Shift+F', QStyle.SP_FileDialogDetailedView)
    w.format_document_action.setEnabled(False)
    edit.addAction(w.format_document_action)
    from notepadview.text_types import TEXT_TYPES
    type_menu = edit.addMenu('文本类型')
    type_group = QActionGroup(w)
    w.type_actions = {}
    for key, item in TEXT_TYPES.items():
        action = a(item.label, lambda _=False, k=key: w.change_document_language(k))
        action.setCheckable(True)
        type_group.addAction(action)
        type_menu.addAction(action)
        w.type_actions[key] = action
    w.settings_menu = QMenu('设置', w)
    w.view_actions = {}
    group = QActionGroup(w)
    for key, label, shortcut in [('source', '仅源码', 'Ctrl+1'), ('split', '双栏', 'Ctrl+2'), ('preview', '仅预览', 'Ctrl+3')]:
        action = a(label, lambda _=False, k=key: w.set_mode(k), shortcut)
        action.setCheckable(True)
        group.addAction(action)
        w.settings_menu.addAction(action)
        w.addAction(action)
        w.view_actions[key] = action
    w.sidebar_action = a('侧边栏', w.set_sidebar_visible, 'Ctrl+Shift+B')
    w.sidebar_action.setCheckable(True)
    w.sidebar_action.setChecked(True)
    w.settings_menu.addSeparator()
    w.settings_menu.addAction(w.sidebar_action)
    w.addAction(w.sidebar_action)
    w.sidebar_position_menu = w.settings_menu.addMenu('侧栏位置')
    positions = QActionGroup(w)
    w.sidebar_position_actions = {}
    for key, label in [('left', '左侧'), ('right', '右侧')]:
        action = a(label, lambda _=False, k=key: w.set_sidebar_position(k))
        action.setCheckable(True)
        positions.addAction(action)
        w.sidebar_position_menu.addAction(action)
        w.sidebar_position_actions[key] = action
    w.theme_menu = QMenu('主题', w)
    w.theme_actions = {}
    themes = QActionGroup(w)
    for key, label in [('nv', 'NV 主题'), ('light', '浅色主题'), ('dark', '深色主题'), ('soft', '护眼主题')]:
        action = a(label, lambda _=False, k=key: w.set_theme(k))
        action.setCheckable(True)
        themes.addAction(action)
        w.theme_menu.addAction(action)
        w.theme_actions[key] = action
    w.api_workbench_action = a(
        'API 工作台', lambda *_: w.set_main_workspace('api'))
    w.api_workbench_action.setCheckable(True)
    w.api_workbench_action.setEnabled(False)
    w.api_workbench_action.setVisible(False)
    menu_bar.addAction(w.api_workbench_action)
    help_menu = menu_bar.addMenu('帮助')
    w.help_action = a('使用帮助', w.show_help, 'F1')
    help_menu.addAction(w.help_action)
    help_menu.addSeparator()
    help_menu.addAction(a('关于 Notepad View', lambda: QMessageBox.about(w, 'Notepad View',
                         'Notepad View 1.0.0\n\n文档编辑器\n\n支持 Markdown、TXT、JSON、XML、SQL。')))


def icon_button(parent, name, tooltip):
    button = QToolButton(parent)
    button.setObjectName(name)
    button.setFixedSize(32, 32)
    button.setIconSize(QSize(18, 18))
    button.setAutoRaise(True)
    button.setToolButtonStyle(Qt.ToolButtonIconOnly)
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    return button


def build_header_controls(w):
    corner = w.menuBar().cornerWidget(Qt.TopRightCorner)
    w.theme_button = icon_button(corner, 'headerButton', '主题')
    w.theme_button.setMenu(w.theme_menu)
    w.theme_button.setPopupMode(QToolButton.InstantPopup)
    w.split_button = icon_button(corner, 'headerButton', '双栏 (Ctrl+2)')
    w.split_button.setCheckable(True)
    w.split_button.clicked.connect(w.toggle_split)
    w.settings_button = icon_button(corner, 'headerButton', '设置')
    w.settings_button.setMenu(w.settings_menu)
    w.settings_button.setPopupMode(QToolButton.InstantPopup)
    w.header_buttons = {'theme': w.theme_button, 'split': w.split_button, 'settings': w.settings_button}
    for index, button in enumerate(w.header_buttons.values()):
        corner.layout().insertWidget(index, button)
    corner.layout().insertSpacing(3, 10)


def build_ui(w, page_class):
    build_header_controls(w)
    root = QWidget()
    layout = QVBoxLayout(root)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    build_find_bar(w)
    w.outer_split = QSplitter(Qt.Horizontal)
    w.outer_split.setChildrenCollapsible(False)
    w.sidebar = QWidget()
    w.sidebar.setObjectName('sidebar')
    w.sidebar.setMinimumWidth(215)
    side = QVBoxLayout(w.sidebar)
    side.setContentsMargins(10, 0, 10, 10)
    side.setSpacing(0)
    w.sidebar_title = QLabel('文件')
    w.sidebar_title.setObjectName('sidebarTitle')
    w.sidebar_title.setFixedHeight(42)
    side.addWidget(w.sidebar_title)
    w.sidebar_stack = QStackedWidget()
    w.sidebar_stack.setObjectName('sidebarStack')
    files_page = QWidget()
    files_page.setObjectName('filesPage')
    files_layout = QVBoxLayout(files_page)
    files_layout.setContentsMargins(0, 0, 0, 0)
    w.file_list = FileTree(w)
    files_layout.addWidget(w.file_list, 1)
    w.outline = QListWidget()
    w.outline.setObjectName('outlineList')
    w.outline.itemClicked.connect(w.jump_heading)
    w.directory_label = QLabel('目录')
    w.directory_label.setObjectName('sectionLabel')
    w.directory_label.setVisible(False)
    w.sidebar_stack.addWidget(files_page)
    w.sidebar_stack.addWidget(w.outline)
    side.addWidget(w.sidebar_stack, 1)
    w.side_filename = QLabel()
    w.side_filename.setWordWrap(True)
    w.side_filename.setObjectName('mutedLabel')
    w.side_filename.setVisible(False)
    side.addWidget(w.side_filename)
    w.editor_split = QSplitter(Qt.Horizontal)
    w.editor_split.setChildrenCollapsible(False)
    w.source_panel = QWidget()
    w.source_panel.setObjectName('sourcePanel')
    source = QVBoxLayout(w.source_panel)
    source.setSpacing(0)
    source.setContentsMargins(0, 0, 0, 0)
    build_format_bar(w, source)
    w.run_button = QToolButton(w.source_panel)
    w.run_button.setText('▶ 运行')
    w.run_button.setToolTip('保存并在独立终端中运行当前脚本')
    w.run_button.clicked.connect(w.run_script)
    w.run_button.hide()
    source.addWidget(w.run_button, 0, Qt.AlignRight)
    w.editor = SourceEditor()
    source.addWidget(w.editor)
    w.editor_scroll_controls = FloatingScrollControls(w.source_panel, w.scroll_editor_top, w.scroll_editor_bottom)
    w.editor_scroll_top_button = w.editor_scroll_controls.top_button
    w.editor_scroll_bottom_button = w.editor_scroll_controls.bottom_button
    w.source_footer = QLabel('  MARKDOWN')
    w.source_footer.setObjectName('paneFooter')
    w.source_footer.setFixedHeight(23)
    source.addWidget(w.source_footer)
    w.preview_panel = QWidget()
    w.preview_panel.setObjectName('previewPanel')
    preview = QVBoxLayout(w.preview_panel)
    preview.setSpacing(0)
    preview.setContentsMargins(0, 0, 0, 0)
    w.preview = PreviewView()
    w.preview.setPage(page_class(w.preview))
    w.preview.settings().setAttribute(QWebEngineSettings.JavascriptEnabled, False)
    w.preview.settings().setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
    preview.addWidget(w.preview, 1)
    footer = QLabel('  HTML')
    footer.setObjectName('paneFooter')
    footer.setFixedHeight(23)
    preview.addWidget(footer)
    w.preview_scroll_controls = FloatingScrollControls(w.preview_panel, w.scroll_preview_top, w.scroll_preview_bottom)
    w.preview_scroll_top_button = w.preview_scroll_controls.top_button
    w.preview_scroll_bottom_button = w.preview_scroll_controls.bottom_button
    w.editor_scroll_top_button.setToolTip('编辑区到顶')
    w.editor_scroll_bottom_button.setToolTip('编辑区到底')
    w.preview_scroll_top_button.setToolTip('预览区到顶')
    w.preview_scroll_bottom_button.setToolTip('预览区到底')
    w.editor_split.addWidget(w.source_panel)
    w.editor_split.addWidget(w.preview_panel)
    w.editor_split.setSizes([600, 600])
    w.sidebar_group = QWidget()
    w.sidebar_group.setObjectName('sidebarGroup')
    w.sidebar_layout = QHBoxLayout(w.sidebar_group)
    w.sidebar_layout.setContentsMargins(0, 0, 0, 0)
    w.sidebar_layout.setSpacing(0)
    w.sidebar_layout.addWidget(w.sidebar, 1)
    w.sidebar_width = 314
    work_area = QWidget()
    work_area.setObjectName('workArea')
    work = QHBoxLayout(work_area)
    work.setContentsMargins(0, 1, 0, 1)
    work.setSpacing(0)
    workspace = WorkspaceContainer()
    workspace.setObjectName('workspaceContainer')
    workspace_layout = QHBoxLayout(workspace)
    workspace_layout.setContentsMargins(0, 0, 0, 0)
    workspace_layout.setSpacing(0)
    workspace_layout.addWidget(w.outer_split)
    work.addWidget(workspace, 1)
    w.sidebar_rail = QWidget()
    w.sidebar_rail.setObjectName('sidebarRail')
    w.sidebar_rail.setFixedWidth(44)
    rail = QVBoxLayout(w.sidebar_rail)
    rail.setContentsMargins(6, 8, 6, 8)
    rail.setSpacing(8)
    w.sidebar_buttons = {}
    for key, label in [('files', '文件'), ('outline', '目录')]:
        button = icon_button(w.sidebar_rail, 'sidebarButton', label)
        button.setCheckable(True)
        button.clicked.connect(lambda _=False, k=key: w.toggle_sidebar_page(k))
        w.sidebar_buttons[key] = button
        rail.addWidget(button)
    w.sidebar_buttons['files'].setChecked(True)
    rail.addStretch()
    w.sidebar_layout.addWidget(w.sidebar_rail)
    from notepadview.binary_preview import BinaryPreview
    w.binary_preview = BinaryPreview(w)
    w.document_area = QStackedWidget()
    w.document_area.addWidget(w.editor_split)
    w.document_area.addWidget(w.binary_preview)
    w.outer_split.addWidget(w.document_area)
    w.outer_split.addWidget(w.sidebar_group)
    w.outer_split.setSizes([1086, w.sidebar_width])
    layout.addWidget(work_area, 1)
    w.document_tabs = QTabBar(w)
    w.document_tabs.setObjectName('documentTabs')
    w.document_tabs.setTabsClosable(True)
    w.document_tabs.setMovable(True)
    w.document_tabs.setExpanding(False)
    w.document_tabs.setElideMode(Qt.ElideMiddle)
    w.document_tabs.setUsesScrollButtons(True)
    w.document_tabs.setDrawBase(False)
    w.document_tabs.setDocumentMode(True)
    w.document_tabs.setIconSize(QSize(16, 16))
    w.document_tabs.currentChanged.connect(w.activate_document)
    w.document_tabs.tabCloseRequested.connect(w.close_document_tab)
    w.document_tabs.hide()
    w.add_document_tab_button = QToolButton(w)
    w.add_document_tab_button.setObjectName('addDocumentButton')
    w.add_document_tab_button.setFixedSize(30, 30)
    w.add_document_tab_button.setText('+')
    w.add_document_tab_button.setToolTip('新建文档')
    w.add_document_tab_button.clicked.connect(w.choose_new_document)
    w.add_document_tab_button.hide()
    w.document_workspace = root
    w.main_workspace = QStackedWidget()
    w.main_workspace.setObjectName('mainWorkspace')
    w.main_workspace.addWidget(root)
    w.setCentralWidget(w.main_workspace)
    w.path_label = QLabel()
    w.path_label.setMinimumWidth(0)
    w.path_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    w.statusBar().addWidget(w.path_label, 1)
    w.stats_label = QLabel()
    w.statusBar().addPermanentWidget(w.stats_label)
    w.encoding_label = QToolButton(w)
    w.encoding_label.setText('  UTF-8  ')
    w.encoding_label.setAutoRaise(True)
    w.encoding_label.setPopupMode(QToolButton.InstantPopup)
    w.encoding_label.setToolTip('重新读取或转换文本编码')
    w.encoding_menu = QMenu(w.encoding_label)
    from notepadview.document import ENCODING_LABELS
    for convert in (False, True):
        if convert:
            w.encoding_menu.addSeparator()
        for encoding, label in ENCODING_LABELS.items():
            action = w.encoding_menu.addAction(('转为' if convert else '') + label + '编码')
            action.setData((convert, encoding))
            action.triggered.connect(
                lambda _=False, value=encoding, change=convert:
                w.convert_encoding(value) if change else w.reload_encoding(value))
    w.encoding_menu.aboutToShow.connect(w.update_encoding_menu)
    w.encoding_label.setMenu(w.encoding_menu)
    w.statusBar().addPermanentWidget(w.encoding_label)
    w.addAction(w.action('关闭查找', w.hide_find, 'Esc'))


class FindReplaceBar(QWidget):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setObjectName('findBar')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.anchor = None
        self.preferred_width = 420
        self._resize_start = None
        self.resize_handle = QWidget(self)
        self.resize_handle.setObjectName('findResizeHandle')
        self.resize_handle.setCursor(Qt.SizeHorCursor)
        self.resize_handle.setToolTip('拖动调整查找栏宽度')
        self.resize_handle.installEventFilter(self)

    def attach(self, panel, anchor):
        visible = not self.isHidden()
        if self.anchor is not None:
            self.anchor.removeEventFilter(self)
        self.setParent(panel)
        self.anchor = anchor
        anchor.installEventFilter(self)
        self.reposition()
        self.setVisible(visible)
        self.raise_()

    def reposition(self):
        if self.anchor is None:
            return
        area = self.anchor.geometry()
        width = min(self.preferred_width, area.width())
        self.resize(width, self.sizeHint().height())
        self.move(area.right() - self.width() + 1, area.top())
        self.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resize_handle.setGeometry(0, 0, 6, self.height())
        self.resize_handle.raise_()

    def eventFilter(self, watched, event):
        if watched is self.resize_handle:
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._resize_start = (event.globalPosition().x(), self.width())
                return True
            if event.type() == QEvent.MouseMove and self._resize_start is not None:
                start_x, start_width = self._resize_start
                self.preferred_width = max(320, int(start_width + start_x - event.globalPosition().x()))
                self.reposition()
                return True
            if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                self._resize_start = None
                return True
        if watched is self.anchor and event.type() in (QEvent.Resize, QEvent.Move, QEvent.Show):
            self.reposition()
        return super().eventFilter(watched, event)

    def closeEvent(self, event):
        self.owner.hide_find()
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.owner.hide_find()
            return
        super().keyPressEvent(event)


def build_find_bar(w):
    w.find_bar = FindReplaceBar(w)
    row = QGridLayout(w.find_bar)
    row.setContentsMargins(10, 8, 10, 8)
    row.setHorizontalSpacing(6)
    row.setVerticalSpacing(6)
    row.setColumnStretch(0, 1)
    w.find_close_button = QToolButton(w.find_bar)
    w.find_close_button.setAutoRaise(True)
    w.find_close_button.setFixedSize(28, 28)
    w.find_close_button.setIconSize(QSize(18, 18))
    w.find_close_button.setToolTip('关闭查找与替换 (Esc)')
    w.find_close_button.setAccessibleName('关闭查找与替换')
    w.find_close_button.clicked.connect(w.hide_find)
    row.addWidget(w.find_close_button, 0, 6, Qt.AlignTop)
    w.find_input = QLineEdit()
    w.find_input.setPlaceholderText('查找内容')
    w.find_input.setTextMargins(0, 0, 86, 0)
    options = QHBoxLayout(w.find_input)
    options.setContentsMargins(0, 1, 2, 1)
    options.setSpacing(0)
    options.addStretch()
    w.find_input.returnPressed.connect(w.find_next)
    w.replace_input = QLineEdit()
    w.replace_input.setPlaceholderText('替换为')
    w.replace_input.setClearButtonEnabled(True)
    w.replace_input.returnPressed.connect(w.replace_one)
    for index, (text, field) in enumerate((('查找', w.find_input), ('替换为', w.replace_input))):
        field.setFixedHeight(32)
        field.setAccessibleName(text)
        field.setMinimumWidth(100)
        row.addWidget(field, index, 0)
    for name, text, callback, position in [
        ('find_previous_button', '↑', lambda: w.find_next(backward=True), (0, 4)),
        ('find_next_button', '↓', w.find_next, (0, 5)),
        ('replace_button', '替换', w.replace_one, (1, 4)),
        ('replace_all_button', '全部替换', w.replace_all, (1, 5)),
    ]:
        button = QToolButton(w.find_bar)
        button.setAutoRaise(True)
        button.setFixedSize(28, 28)
        button.setIconSize(QSize(18, 18))
        button.setToolTip({'find_previous_button': '上一个', 'find_next_button': '下一个'}.get(name, text))
        button.setAccessibleName(button.toolTip())
        button.clicked.connect(callback)
        setattr(w, name, button)
        row.addWidget(button, *position)
    for name, text, tooltip in [
        ('case_checkbox', 'Aa', '区分大小写'),
        ('whole_word_checkbox', 'ab', '全字匹配'),
        ('regex_checkbox', '.*', '使用正则表达式'),
    ]:
        button = QToolButton(w.find_input)
        button.setAutoRaise(True)
        button.setText(text)
        button.setCheckable(True)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setFixedSize(28, 28)
        button.toggled.connect(lambda: w.find_feedback.clear())
        setattr(w, name, button)
        options.addWidget(button)
    w.find_feedback = QLabel()
    w.find_feedback.setObjectName('findFeedback')
    w.find_feedback.setTextFormat(Qt.RichText)
    w.find_feedback.setWordWrap(True)
    w.find_feedback.setMinimumHeight(20)
    row.addWidget(w.find_feedback, 2, 0, 1, 7)
    w.find_input.textChanged.connect(lambda: w.find_feedback.clear())
    w.replace_input.textChanged.connect(lambda: w.find_feedback.clear())
    w.regex_checkbox.toggled.connect(lambda: w.find_feedback.clear())
    for button in w.find_bar.findChildren(QToolButton):
        button.setCursor(Qt.PointingHandCursor)
    w.find_bar.hide()


def build_format_bar(w, layout):
    bar = QToolBar()
    bar.setObjectName('formatbar')
    bar.setIconSize(QSize(16, 16))
    layout.addWidget(bar)
    w.language_button = QToolButton(bar)
    w.language_button.setObjectName('languageButton')
    w.language_button.setText('语言')
    w.language_button.setToolTip('切换语言和文件扩展名')
    w.language_button.setAccessibleName('切换文档语言和文件类型')
    w.language_button.setPopupMode(QToolButton.InstantPopup)
    language_menu = QMenu(w.language_button)
    language_menu.addActions(list(w.type_actions.values()))
    w.language_button.setMenu(language_menu)
    bar.addWidget(w.language_button)
    bar.addSeparator()
    w.format_actions = []
    for label, tooltip, prefix, suffix, default, shortcut in [
        ('B', '粗体', '**', '**', '粗体文字', 'Ctrl+B'),
        ('I', '斜体', '*', '*', '斜体文字', 'Ctrl+I'),
        ('H', '二级标题', '## ', '', '标题', 'Ctrl+Alt+2'),
        ('<>', '行内代码', '`', '`', 'code', 'Ctrl+E'),
        ('❞', '引用', '> ', '', '引用内容', None),
        ('☷', '无序列表', '- ', '', '列表项', None),
        ('☑', '待办事项', '- [ ] ', '', '待办事项', None),
    ]:
        action = w.action(label, lambda _=False, p=prefix, s=suffix, d=default: w.wrap_selection(p, s, d), shortcut)
        action.setToolTip(tooltip + (f' ({shortcut})' if shortcut else ''))
        font = action.font()
        if label == 'B':
            font.setBold(True)
        if label == 'I':
            font.setItalic(True)
        action.setFont(font)
        bar.addAction(action)
        w.format_actions.append(action)
    strike = w.action('S', lambda: w.editor.apply_markdown_inline('~~', '~~', '删除线文字'))
    strike.setToolTip('删除线')
    font = strike.font()
    font.setStrikeOut(True)
    strike.setFont(font)
    bar.addAction(strike)
    w.format_actions.append(strike)
    color = w.action('字体颜色', lambda: w.editor.apply_markdown_color())
    color.setToolTip('设置选中文字的颜色')
    bar.addAction(color)
    w.format_actions.append(color)
    bar.addSeparator()
    link = w.action('↗', w.insert_link)
    link.setToolTip('插入链接')
    bar.addAction(link)
    w.format_actions.append(link)
    image = w.action('图片', w.insert_image, standard=QStyle.SP_FileDialogContentsView)
    bar.addAction(image)
    w.format_actions.append(image)
    bar.addSeparator()
    bar.addAction(w.format_document_action)
