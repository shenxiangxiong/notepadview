from dataclasses import dataclass, field
from html import escape
import json
import logging
from pathlib import Path
import re
import sqlite3
from threading import Event, Thread
from urllib.parse import quote
from uuid import uuid4

from PySide6.QtCore import (QSettings, QStandardPaths, QSize, QTemporaryDir,
                            QThreadPool, QTimer, QUrl, Qt)
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QInputDialog, QListWidgetItem,
                              QMessageBox, QStyle, QTabBar)
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineScript, QWebEngineSettings

from notepadview import script_runner

from notepadview.document import (BinaryDocumentError, ENCODING_LABELS, encode_text,
                                  load_text_document, save_document)
from notepadview.text_types import TEXT_TYPES, detect_text_type, type_for_path, save_filter
from notepadview.binary_preview import file_kind
from notepadview.office_reader import convert_office_to_pdf, is_office_reader
from notepadview.ebook_reader import (EBOOK_CONVERT_SUFFIXES, convert_ebook_to_epub,
                                      read_epub)
from notepadview.reader_conversion import ReaderConversionService
from notepadview.reader_jobs import ReaderJob
from notepadview.reader_runtime import reader_runtime_root
from notepadview.rendering import (COMPACT_HIGHLIGHT_SCRIPT, MAX_PREVIEW_HIGHLIGHT_BYTES, markdown_headings,
                               render_code_preview, render_html, render_plain_text)
from notepadview.editor import SourceDocument
from notepadview.ui import build_ui, build_actions
from notepadview.theme import apply_theme
from notepadview.palettes import THEMES
from notepadview.localization import install_chinese_translations
from notepadview.dialogs import NewDocumentDialog
from notepadview.file_drop import FileDropFilter
from notepadview.errors import chinese_error
from notepadview.window_chrome import ChromeWindow
from notepadview.search import BoundedSearch, SearchError

ASSETS = Path(__file__).resolve().parent / 'assets'
_FIND_COUNT_PATTERN = re.compile(r'\d+')
_FIND_FEEDBACK_COLORS = {
    'light': ('#188038', '#c5221f'),
    'soft': ('#188038', '#c5221f'),
    'dark': ('#81c995', '#f28b82'),
    'nv': ('#81c995', '#f28b82'),
}


class PreviewPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        if navigation_type == QWebEnginePage.NavigationTypeLinkClicked:
            if url.hasFragment() and url.adjusted(QUrl.RemoveFragment) == self.url().adjusted(QUrl.RemoveFragment):
                return True
            if url.scheme() in ('https', 'http', 'mailto'):
                QDesktopServices.openUrl(url)
            return False
        return True


@dataclass
class DocumentSession:
    document: SourceDocument
    path: Path | None = None
    document_format: str = 'md'
    manual_format: bool = False
    selection: dict | None = None
    first_visible_line: int = 0
    id: str = field(default_factory=lambda: uuid4().hex)
    category_id: str = ''
    preview_position: float = 0
    display_name: str = ''
    kind: str = 'text'
    encoding: str = 'utf-8'
    newline: str = '\n'
    preview_state: dict = field(default_factory=dict)
    diagram: object = None
    image_editor: object = None
    render_path: Path | None = None
    reader_error: str | None = None
    reader_generation: int = 0
    ebook_widget: object = None
    reader_state: dict = field(default_factory=dict)
    view_mode: str | None = None
    last_single_view: str = 'source'
    format_suffix: str | None = None
    untyped: bool = False
    _text_format_key: tuple | None = field(default=None, init=False, repr=False)

    def __post_init__(self):
        if self.format_suffix is None and self.path is not None:
            self.format_suffix = self.path.suffix.casefold()

    def refresh_text_format(self, source=None):
        if self.kind != 'text' or self.path is None:
            return
        key = (self.path, self.document.revision, self.manual_format, self.document_format)
        if source is None and key == self._text_format_key:
            return
        suffix = self.path.suffix.casefold()
        inferred = type_for_path(self.path)
        if ((self.format_suffix is not None and suffix != self.format_suffix)
                or (self.format_suffix is None and inferred != 'txt'
                    and inferred != self.document_format)):
            self.manual_format = False
        self.format_suffix = suffix
        if not self.manual_format:
            if inferred != 'txt':
                self.document_format = inferred
            else:
                if source is None:
                    source = bytes(self.document.get_char_range(0, self.document.length())).decode('utf-8')
                self.document_format = detect_text_type(self.path, source)
        self._text_format_key = (self.path, self.document.revision, self.manual_format, self.document_format)

    @property
    def name(self):
        return self.path.name if self.path else self.display_name or ('未命名' if self.untyped else f'未命名.{TEXT_TYPES[self.document_format].suffixes[0]}')


class MainWindow(ChromeWindow):
    def __init__(self, settings=None):
        super().__init__()
        install_chinese_translations(QApplication.instance())
        self.isolated = settings is not None
        self.settings = settings if settings is not None else QSettings('NotepadView', 'NotepadView')
        self.api_workbench = None
        self.api_workbench_store = None
        self.main_workspace_name = 'documents'
        self.sessions = []
        cache_root = Path(QStandardPaths.writableLocation(
            QStandardPaths.CacheLocation)) / 'reader-conversions'
        self.reader_conversion = ReaderConversionService(cache_root, reader_runtime_root())
        self.reader_pool = QThreadPool(self)
        self._reader_jobs = {}
        self.categories = []
        self.default_category_name = self.settings.value('default_category_name', '默认分类', type=str).strip() or '默认分类'
        if self.default_category_name == '未分类':
            self.default_category_name = '默认分类'
            self.settings.setValue('default_category_name', self.default_category_name)
        self.workspace = None
        self.current_session = None
        self._bound_document = None
        self._suspend_document_events = False
        self.path = None
        self.new_document_format = 'md'
        self.is_plain_text = False
        self.theme = self.settings.value('theme', 'nv')
        self.default_view = 'source'
        self.mode = 'source'
        self.last_single_view = 'source'
        self.sidebar_position = self.settings.value('sidebar_position', 'right')
        if self.sidebar_position not in ('left', 'right'):
            self.sidebar_position = 'right'
        self.recent = self.settings.value('recent', [], type=list)
        self.scroll_position = 0
        self._preview_ready = False
        self._preview_generation = 0
        self._preview_target = None
        self._preview_work = None
        self._pending_preview = None
        self._preview_directory = QTemporaryDir()
        self._outline_key = None
        self.resize(1400, 900)
        self.setMinimumSize(880, 580)
        self.setWindowIcon(QIcon(str(ASSETS / 'app.ico')))
        build_actions(self)
        build_ui(self, PreviewPage)
        self.set_sidebar_position(self.sidebar_position)
        self.set_mode(self.mode)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(220)
        self.timer.timeout.connect(self.render_preview)
        self.preview_work_timer = QTimer(self)
        self.preview_work_timer.setInterval(30)
        self.preview_work_timer.timeout.connect(self._poll_preview_work)
        self.stats_timer = QTimer(self)
        self.stats_timer.setSingleShot(True)
        self.stats_timer.setInterval(180)
        self.stats_timer.timeout.connect(self.update_statistics)
        self._stats_work = None
        self._pending_stats = None
        self.stats_work_timer = QTimer(self)
        self.stats_work_timer.setInterval(25)
        self.stats_work_timer.timeout.connect(self._poll_statistics_work)
        self.outline_timer = QTimer(self)
        self.outline_timer.setSingleShot(True)
        self.outline_timer.setInterval(300)
        self.outline_timer.timeout.connect(self.update_outline)
        self._outline_work = None
        self._pending_outline = None
        self.outline_work_timer = QTimer(self)
        self.outline_work_timer.setInterval(30)
        self.outline_work_timer.timeout.connect(self._poll_outline_work)
        self._stats_count = 0
        self._stats_char_count = 0
        self.editor.cursorPositionChanged.connect(self.update_status)
        self.preview.loadFinished.connect(self._preview_loaded)
        self.preview.page().scrollPositionChanged.connect(lambda pos: setattr(self, 'scroll_position', pos.y()))
        from notepadview.scroll_sync import ScrollSync
        self.scroll_sync = ScrollSync(self)
        from notepadview.selection_sync import SelectionSync
        self.selection_sync = SelectionSync(self)
        self.set_theme(self.theme)
        self.editor.textChanged.connect(self._changed)
        self.refresh_recent()
        geometry = self.settings.value('geometry')
        if geometry:
            self.restoreGeometry(geometry)
        self.layout().activate()
        self._resize_sidebar(self.sidebar_width)
        self.update_title()
        self.update_outline()
        self.update_statistics()
        self.file_drop_filter = FileDropFilter(self)
        from notepadview.workspace import WorkspaceController
        self.workspace = WorkspaceController(self, isolated=settings is not None)
        self.workspace.restore()
        self.editor.cursorPositionChanged.connect(self.schedule_workspace_save)

    def action(self, text, callback, shortcut=None, standard=None):
        action = QAction(text, self)
        if standard is not None:
            action.setIcon(self.style().standardIcon(standard))
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(callback)
        return action

    @property
    def is_readonly_preview(self):
        return self.current_session is not None and self.current_session.kind != 'text'

    @property
    def is_diagram(self):
        return self.current_session is not None and self.current_session.kind == 'diagram'

    def _attach_diagram(self, session, model, modified=False):
        from notepadview.diagrams import DiagramEditor
        if session.diagram is not None:
            self.document_area.removeWidget(session.diagram)
            session.diagram.deleteLater()
        canvas = DiagramEditor(model, self)
        session.diagram = canvas
        self.document_area.addWidget(canvas)
        if modified:
            canvas.undo_stack.resetClean()
        def changed():
            session.document.setModified(not canvas.undo_stack.isClean())
            if self.current_session is session:
                self.undo_action.setEnabled(canvas.undo_stack.canUndo())
                self.redo_action.setEnabled(canvas.undo_stack.canRedo())
            self.update_title()
            self.schedule_workspace_save()
        canvas.changed.connect(changed)
        session.document.setModified(modified)

    def new_diagram(self, kind='flowchart'):
        from notepadview.diagrams import new_diagram
        self.new_document('txt')
        session = self.current_session
        session.kind = 'diagram'
        session.display_name = '未命名.nvdiagram'
        self._attach_diagram(session, new_diagram(kind), modified=True)
        self.update_document_mode()
        self.update_title()
        self.schedule_workspace_save()
        return True

    def _attach_image(self, session, editor):
        self._release_image(session)
        if session.kind in ('docx', 'xlsx'):
            editor.set_view_only()
        session.image_editor = editor
        editor.setParent(self)
        self.document_area.addWidget(editor)
        def changed():
            session.document.setModified(not editor.undo_stack.isClean())
            if self.current_session is session:
                self.undo_action.setEnabled(editor.undo_stack.canUndo())
                self.redo_action.setEnabled(editor.undo_stack.canRedo())
            self.update_title()
            self.schedule_workspace_save()
        editor.changed.connect(changed)
        session.document.setModified(not editor.undo_stack.isClean())

    def _release_image(self, session):
        if session.image_editor is not None:
            self.document_area.removeWidget(session.image_editor)
            session.image_editor.deleteLater()
            session.image_editor = None

    def undo_current(self):
        if self.current_session and self.current_session.kind in ('docx', 'xlsx'):
            return
        if self.current_session and self.current_session.image_editor is not None:
            self.current_session.image_editor.undo_stack.undo()
        else:
            (self.current_session.diagram.undo_stack if self.is_diagram else self.editor).undo()

    def redo_current(self):
        if self.current_session and self.current_session.kind in ('docx', 'xlsx'):
            return
        if self.current_session and self.current_session.image_editor is not None:
            self.current_session.image_editor.undo_stack.redo()
        else:
            (self.current_session.diagram.undo_stack if self.is_diagram else self.editor).redo()

    def edit_current(self, action):
        session = self.current_session
        if session is not None and session.kind in ('docx', 'xlsx'):
            session.image_editor.edit_action(action)
        elif not self.is_readonly_preview:
            getattr(self.editor, action)()

    def copy_current(self):
        if self.current_session is not None and self.current_session.kind in ('docx', 'xlsx'):
            self.edit_current('copy')
            return
        if self.is_readonly_preview:
            return
        focus = QApplication.focusWidget()
        if self.preview.hasFocus() or (focus is not None and self.preview.isAncestorOf(focus)):
            try:
                web_action = QWebEnginePage.WebAction.Copy
            except AttributeError:
                web_action = QWebEnginePage.Copy
            self.preview.page().triggerAction(web_action)
            return
        self.editor.copy()

    def _new_source_document(self, text=''):
        document = SourceDocument(self)
        document.setPlainText(text)
        document.setModified(False)
        return document

    def set_theme(self, theme):
        self.theme = theme if theme in THEMES else 'nv'
        self._suspend_document_events = True
        try:
            apply_theme(self)
        finally:
            self._suspend_document_events = False
        if self.api_workbench is not None:
            self.api_workbench.set_theme(self.theme)
        for session in self.sessions:
            if session.ebook_widget is not None:
                session.ebook_widget.set_theme(self.theme)
        self.settings.setValue('theme', self.theme)
        self._schedule_preview()

    def set_main_workspace(self, name):
        if name not in ('documents', 'api'):
            raise ValueError(f'Unknown main workspace: {name}')
        if name == 'api' and not self.api_workbench_action.isEnabled():
            return False
        if name == 'api' and self.api_workbench is None:
            store = None
            try:
                from notepadview.api_workbench.secrets import SecretStorageError, SecretVault
                from notepadview.api_workbench.store import ApiWorkbenchStore
                from notepadview.api_workbench.ui import ApiWorkbenchPage

                if self.isolated:
                    database = Path(self.settings.fileName() + '.api-workbench.sqlite3')
                else:
                    database = (
                        Path(QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation))
                        / 'Notepad View' / 'api-workbench.sqlite3'
                    )
                store = ApiWorkbenchStore(database, vault=SecretVault())
                page = ApiWorkbenchPage(
                    store, on_return=lambda: self.set_main_workspace('documents'))
            except (SecretStorageError, OSError, ValueError, RuntimeError, sqlite3.Error) as error:
                if store is not None:
                    store.close()
                QMessageBox.critical(self, 'API 工作台不可用', str(error))
                return False
            self.api_workbench_store = store
            self.api_workbench = page
            self.api_workbench.set_theme(self.theme)
            self.main_workspace.addWidget(self.api_workbench)
        if name == 'api':
            self.main_workspace.setCurrentWidget(self.api_workbench)
        else:
            self.main_workspace.setCurrentWidget(self.document_workspace)
        self.main_workspace_name = name
        self.api_workbench_action.setChecked(name == 'api')
        return True

    def set_mode(self, mode, *, remember=True):
        if mode not in self.view_actions:
            return
        if self.is_readonly_preview:
            return
        self.mode = mode
        self._position_find_bar()
        self.source_panel.setVisible(mode != 'preview')
        self.preview_panel.setVisible(mode != 'source')
        self.split_button.setChecked(mode == 'split')
        if mode == 'split':
            self.editor_split.setSizes([max(1, self.editor_split.width() // 2)] * 2)
            if getattr(self, '_preview_ready', False) and hasattr(self, 'selection_sync'):
                QTimer.singleShot(0, self.selection_sync, self.selection_sync.loaded)

        self.view_actions[mode].setChecked(True)
        if mode in ('source', 'preview'):
            self.last_single_view = mode

    def show_help(self):
        from notepadview.help import HelpDialog
        if getattr(self, 'help_dialog', None) is None:
            self.help_dialog = HelpDialog(self)
        self.help_dialog.show()
        self.help_dialog.raise_()
        self.help_dialog.activateWindow()

    def toggle_split(self):
        self.set_mode(self.last_single_view if self.mode == 'split' else 'split')

    def set_sidebar_visible(self, visible):
        if not self.sidebar.isHidden():
            self.sidebar_width = self.sidebar_group.width()
        self.sidebar.setVisible(visible)
        self.sidebar_group.setMinimumWidth(259 if visible else 44)
        self.sidebar_group.setMaximumWidth(16777215 if visible else 44)
        self._resize_sidebar(self.sidebar_width if visible else 44)
        self.sidebar_action.setChecked(visible)
        active = 'files' if self.sidebar_stack.currentIndex() == 0 else 'outline'
        for key, button in self.sidebar_buttons.items():
            button.setChecked(visible and key == active)

    def _resize_sidebar(self, width):
        available = max(1, self.outer_split.width() - self.outer_split.handleWidth())
        sizes = [max(1, available - width), width]
        self.outer_split.setSizes(sizes if self.sidebar_position == 'right' else sizes[::-1])

    def set_sidebar_position(self, position):
        if position not in self.sidebar_position_actions:
            return
        visible = not self.sidebar.isHidden()
        width = self.sidebar_group.width() if visible else 44
        self.sidebar_position = position
        self.outer_split.insertWidget(0 if position == 'left' else 1, self.sidebar_group)
        self.sidebar_layout.removeWidget(self.sidebar_rail)
        self.sidebar_layout.insertWidget(0 if position == 'left' else 1, self.sidebar_rail)
        self.outer_split.setStretchFactor(self.outer_split.indexOf(self.document_area), 1)
        self.outer_split.setStretchFactor(self.outer_split.indexOf(self.sidebar_group), 0)
        self.sidebar_group.setProperty('position', position)
        self.sidebar_rail.setProperty('position', position)
        self.sidebar_rail.style().unpolish(self.sidebar_rail)
        self.sidebar_rail.style().polish(self.sidebar_rail)
        self._resize_sidebar(width)
        self.sidebar_position_actions[position].setChecked(True)
        self.settings.setValue('sidebar_position', position)

    def toggle_sidebar_page(self, page):
        index = {'files': 0, 'outline': 1}[page]
        if index == self.sidebar_stack.currentIndex() and not self.sidebar.isHidden():
            self.set_sidebar_visible(False)
            return
        self.sidebar_stack.setCurrentIndex(index)
        self.sidebar_title.setText('文件' if page == 'files' else '目录')
        self.set_sidebar_visible(True)

    def _changed(self):
        if self._suspend_document_events or self.is_readonly_preview:
            return
        if self.current_session is None:
            self._adopt_current_document()
        self._preview_generation += 1
        self._preview_ready = False
        self._pending_preview = None
        self.timer.start()
        self.stats_timer.start()
        if self.editor.length() <= 16 * 1024:
            self.update_outline()
        else:
            self.outline_timer.start()
        self.update_status()
        self.schedule_workspace_save()

    def _adopt_current_document(self):
        document = self.editor.document()
        document.setParent(self)
        session = DocumentSession(document=document)
        self.sessions.append(session)
        self.current_session = session
        self.document_tabs.addTab('未命名.md')
        self.document_tabs.setCurrentIndex(self.document_tabs.count() - 1)
        self.document_tabs.setTabData(self.document_tabs.currentIndex(), session)
        self._bind_document(session)
        self.update_title()

    def _preview_cache_key(self, session):
        return (id(session), session.document.revision, str(session.path),
                session.document_format, self.theme)

    def _cache_active_preview(self):
        session = self.current_session
        if session is None or session.kind != 'text' or not self._preview_ready:
            return
        key = self._preview_cache_key(session)
        if (session.document_format == 'html'
                or key != getattr(self, '_preview_content_key', None)):
            return
        cache = getattr(self, '_preview_cache', {})
        cache.pop(key, None)
        cache[key] = self.selection_sync.mapping if self.selection_sync.enabled() else None
        while len(cache) > 2:
            del cache[next(iter(cache))]
        self._preview_cache = cache
        self.preview.page().runJavaScript('''((key) => {
            const cache = globalThis.nvDocumentCache ||= new Map();
            cache.delete(key);
            cache.set(key, {head: [...document.head.childNodes],
                body: [...document.body.childNodes],
                attributes: [...document.documentElement.attributes].map(a => [a.name, a.value]),
                scroll: globalThis.nvScrollMap, selection: globalThis.nvSelection});
            while (cache.size > 2) cache.delete(cache.keys().next().value);
        })(''' + json.dumps(repr(key)) + ')', QWebEngineScript.ApplicationWorld)

    def _restore_cached_preview(self, session):
        key = self._preview_cache_key(session)
        cache = getattr(self, '_preview_cache', {})
        if key not in cache or self.new_document_format == 'html':
            return False
        generation = self._preview_generation
        mapping = cache[key]
        script = '''((key, generation) => {
            const saved = globalThis.nvDocumentCache?.get(key);
            if (!saved) return false;
            getSelection().removeAllRanges();
            document.head.replaceChildren(...saved.head);
            document.body.replaceChildren(...saved.body);
            const root = document.documentElement;
            for (const attribute of [...root.attributes]) root.removeAttribute(attribute.name);
            for (const [name, value] of saved.attributes) root.setAttribute(name, value);
            root.dataset.previewGeneration = generation;
            globalThis.nvScrollMap = saved.scroll;
            globalThis.nvSelection = saved.selection;
            return true;
        })(''' + json.dumps(repr(key)) + ', ' + json.dumps(str(generation)) + ')'

        def restored(success):
            if generation != self._preview_generation:
                return
            if not success:
                self.render_preview()
                return
            self._preview_ready = True
            self._preview_content_key = key
            self.scroll_sync.reset()
            if mapping is not None:
                sync = self.selection_sync
                sync.mapping = mapping
                sync._generation = generation
                sync._source_range = None
                sync._preview_range = ()
                sync._from_source()
            else:
                self.selection_sync.loaded()
            self._apply_preview_navigation()

        self.preview.page().runJavaScript(script, QWebEngineScript.ApplicationWorld, restored)
        return True

    def _bind_document(self, session):
        self._cache_active_preview()
        self.timer.stop()
        self._preview_ready = False
        self._preview_generation += 1
        self._pending_preview = None
        self.selection_sync._invalidate()
        self.scroll_sync.reset()
        session.refresh_text_format()
        if self.current_session is not None and self.current_session is not session:
            self.current_session.selection = self.editor.selection_state()
            self.current_session.first_visible_line = self.editor.firstVisibleLine()
            self.current_session.preview_position = self.scroll_position
            if self.current_session.kind in ('image', 'pdf', 'office_pdf'):
                self.current_session.preview_state = self.binary_preview.state()
            elif self.current_session.kind == 'ebook' and self.current_session.ebook_widget is not None:
                self.current_session.reader_state = self.current_session.ebook_widget.state()
        document = session.document
        if self._bound_document is not None:
            try:
                self._bound_document.modificationChanged.disconnect(self.update_title)
                self._bound_document.undoAvailable.disconnect(self.undo_action.setEnabled)
                self._bound_document.redoAvailable.disconnect(self.redo_action.setEnabled)
            except RuntimeError:
                pass
        session.view_mode = 'source'
        session.last_single_view = 'source'
        self.current_session = session
        self.last_single_view = 'source'
        if session.kind == 'text':
            self.set_mode('source', remember=False)
        else:
            self.mode = 'preview'
            self.view_actions['preview'].setChecked(True)
            self.split_button.setChecked(False)
        self.path = session.path
        self.new_document_format = session.document_format
        self.is_plain_text = session.document_format == 'txt'
        self._suspend_document_events = True
        try:
            self.editor.setReadOnly(False)
            self.editor.set_source_document(document, self.is_plain_text, language=session.document_format)
            self.editor.setReadOnly(self.is_readonly_preview)
        finally:
            self._suspend_document_events = False
        self._bound_document = document
        self.editor.restore_selection_state(session.selection)
        self.editor.setFirstVisibleLine(session.first_visible_line)
        document.modificationChanged.connect(self.update_title)
        document.undoAvailable.connect(self.undo_action.setEnabled)
        document.redoAvailable.connect(self.redo_action.setEnabled)
        self.undo_action.setEnabled(document.isUndoAvailable())
        self.redo_action.setEnabled(document.isRedoAvailable())
        self.update_document_mode()
        if session.kind in ('image', 'pdf', 'office_pdf') and session.image_editor is None:
            self._load_session_preview(session)
        elif session.kind == 'ebook':
            self._load_session_preview(session)
        else:
            self.binary_preview.release()
        self.update_title()
        if self.editor.length() > MAX_PREVIEW_HIGHLIGHT_BYTES:
            self.outline.clear()
            self._outline_key = None
            self.outline_timer.start()
            self._stats_count, self._stats_char_count = document.statistics
            self.stats_timer.start()
        else:
            self.update_outline()
            self.update_statistics()
        self.update_status()
        self.scroll_position = session.preview_position
        self._preview_target = ('scroll', session.preview_position)
        if not self._restore_cached_preview(session):
            self._schedule_preview()
        self.schedule_workspace_save()

    def _load_session_preview(self, session):
        if session.image_editor is not None:
            return
        if session.kind == 'ebook':
            if session.ebook_widget is None:
                from notepadview.ebook_reader_widget import EbookReader
                widget = EbookReader(self)
                widget.set_theme(self.theme)
                widget.stateChanged.connect(
                    lambda session=session: self._ebook_state_changed(session))
                session.ebook_widget = widget
                self.document_area.addWidget(widget)
            self.document_area.setCurrentWidget(session.ebook_widget)
            if session.reader_error:
                session.ebook_widget.show_error(session.reader_error)
            elif session.ebook_widget.book is None:
                session.ebook_widget.title_label.setText('正在读取电子书…')
                self._start_ebook_read(session)
            return
        if session.kind == 'office_pdf':
            if session.reader_error:
                self.binary_preview.release()
                self.binary_preview.caption.setText('预览不可用：' + session.reader_error)
                return
            if session.render_path is None:
                self.binary_preview.release()
                self.binary_preview.caption.setText('正在转换 Office 文档…')
                self._start_office_conversion(session)
                return
            preview_path, preview_kind = session.render_path, 'pdf'
        else:
            preview_path, preview_kind = session.path, session.kind
        try:
            self.binary_preview.load(preview_path, preview_kind, session.preview_state)
        except (OSError, ValueError) as error:
            self.binary_preview.release()
            self.binary_preview.caption.setText('预览不可用：' + str(error))

    def _start_office_conversion(self, session):
        self._start_reader_job(
            session,
            lambda: convert_office_to_pdf(session.path, self.reader_conversion),
        )

    def _start_ebook_read(self, session):
        def read():
            source = session.path
            if source.suffix.casefold() in EBOOK_CONVERT_SUFFIXES:
                source = convert_ebook_to_epub(source, self.reader_conversion)
            return read_epub(source, self.reader_conversion.cache_root)

        self._start_reader_job(session, read)

    def _ebook_state_changed(self, session):
        if session.ebook_widget is None:
            return
        session.reader_state = session.ebook_widget.state()
        self.schedule_workspace_save()

    def _release_ebook(self, session):
        if session.ebook_widget is not None:
            self.document_area.removeWidget(session.ebook_widget)
            session.ebook_widget.deleteLater()
            session.ebook_widget = None

    def _start_reader_job(self, session, operation):
        key = (session.id, session.reader_generation)
        if key in self._reader_jobs:
            return False
        job = ReaderJob(session.id, session.reader_generation, operation)
        job.signals.completed.connect(self._reader_job_completed)
        self._reader_jobs[key] = job
        self.reader_pool.start(job)
        return True

    def _reader_job_completed(self, session_id, generation, result, error):
        self._reader_jobs.pop((session_id, generation), None)
        session = next((item for item in self.sessions if item.id == session_id), None)
        if session is None or session.reader_generation != generation:
            return
        if error:
            session.reader_error = error
            if session is self.current_session:
                if session.kind == 'ebook' and session.ebook_widget is not None:
                    session.ebook_widget.show_error(error)
                else:
                    self.binary_preview.release()
                    self.binary_preview.caption.setText('预览不可用：' + error)
                self.statusBar().showMessage('文件转换失败：' + error, 10000)
            self.schedule_workspace_save()
            return
        if session.kind == 'ebook':
            from notepadview.ebook_reader import EpubBook
            if not isinstance(result, EpubBook):
                error = '阅读引擎返回了无效的电子书结果。'
                session.reader_error = error
                if session is self.current_session and session.ebook_widget is not None:
                    session.ebook_widget.show_error(error)
                self.schedule_workspace_save()
                return
            session.reader_error = None
            if session.ebook_widget is not None:
                session.ebook_widget.load_book(result, session.reader_state)
            self.schedule_workspace_save()
            return
        if session.kind != 'office_pdf' or not isinstance(result, Path) or not result.is_file():
            error = '阅读引擎返回了无效的预览结果。'
            session.reader_error = error
            if session is self.current_session:
                self.binary_preview.release()
                self.binary_preview.caption.setText('预览不可用：' + error)
            self.schedule_workspace_save()
            return
        session.render_path = result
        session.reader_error = None
        if session is self.current_session:
            self._load_session_preview(session)
        self.schedule_workspace_save()

    def activate_document(self, index):
        if index < 0:
            self._clear_active_document()
            return
        session = self.document_tabs.tabData(index)
        if not isinstance(session, DocumentSession) or session is self.current_session:
            return
        self._bind_document(session)

    def _tab_sessions(self):
        return [self.document_tabs.tabData(index)
                for index in range(self.document_tabs.count())
                if isinstance(self.document_tabs.tabData(index), DocumentSession)]

    def _session_tab_index(self, session):
        for index in range(self.document_tabs.count()):
            if self.document_tabs.tabData(index) is session:
                return index
        return -1

    def activate_file_item(self, item):
        session = item.data(0, Qt.UserRole)
        index = self._session_tab_index(session)
        if index >= 0:
            self.document_tabs.setCurrentIndex(index)

    def close_document_session(self, session):
        index = self._session_tab_index(session)
        return self.close_document_tab(index) if index >= 0 else False

    def refresh_file_list(self):
        self.file_list.refresh(self._tab_sessions(), self.current_session)
        for session in self._tab_sessions():
            self._refresh_document_tab(session)

    def _refresh_document_tab(self, session):
        index = self._session_tab_index(session)
        if index < 0:
            return
        dirty = ' •' if session.document.isModified() else ''
        item = self.file_list.item_for_session(session)
        prefix = item.text(0).split('. ', 1)[0] + '. ' if item is not None else ''
        self.document_tabs.setTabText(index, prefix + session.name + dirty)
        self.document_tabs.setTabIcon(index, self.style().standardIcon(QStyle.SP_FileIcon))
        self.document_tabs.setTabToolTip(index, str(session.path) if session.path else session.name)
        close = self.document_tabs.tabButton(index, QTabBar.RightSide)
        if close is not None:
            close.setToolTip('关闭文档')
            close.setAccessibleName('关闭文档')

    def request_rename_document(self, session=None):
        session = self.current_session if session is None else session
        if not isinstance(session, DocumentSession) or not any(item is session for item in self.sessions):
            return False
        suffix = session.path.suffix if session.path else '.' + session.kind if session.kind in ('docx', 'xlsx') else '.nvdiagram' if session.kind == 'diagram' else '.' + TEXT_TYPES[session.document_format].suffixes[0]
        dialog = QInputDialog(self)
        dialog.setWindowTitle('重命名文档')
        dialog.setLabelText(f'文件名（保留 {suffix or "无扩展名"}，可省略后缀）')
        dialog.setTextValue(session.name)
        dialog.setOkButtonText('重命名')
        dialog.setCancelButtonText('取消')
        dialog.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        dialog.setMinimumWidth(620)
        dialog.resize(720, dialog.sizeHint().height())
        try:
            if dialog.exec() != QDialog.Accepted:
                return False
            return self.rename_document_session(session, dialog.textValue())
        finally:
            dialog.deleteLater()

    def open_document_folder(self, session=None):
        session = self.current_session if session is None else session
        if session is None or not any(item is session for item in self.sessions) or session.path is None:
            self.statusBar().showMessage('文档尚未保存，没有所在文件夹。', 4000)
            return False
        try:
            folder = session.path.resolve().parent
            if not folder.is_dir():
                self.statusBar().showMessage('无法打开所在文件夹：目录不存在。', 5000)
                return False
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
                self.statusBar().showMessage('无法打开所在文件夹，请检查系统文件管理器。', 5000)
                return False
        except OSError as error:
            self.statusBar().showMessage('无法打开所在文件夹：' + chinese_error(error), 5000)
            return False
        return True

    @staticmethod
    def _document_rename_name(session, name):
        if not isinstance(name, str) or not name.strip():
            raise ValueError('文件名不能为空。')
        if name[-1].isspace() or name.endswith('.'):
            raise ValueError('文件名不能以空白或句点结尾。')
        if any(ord(character) < 32 or character in '<>:"/\\|?*' for character in name):
            raise ValueError('文件名不能包含路径分隔符、控制字符或以下字符：< > : " / \\ | ? *。')
        base = name.split('.')[0].rstrip(' ').upper()
        if base in ('CON', 'PRN', 'AUX', 'NUL', 'CLOCK$', 'CONIN$', 'CONOUT$') or re.fullmatch(
                r'(?:COM|LPT)[1-9\u00b9\u00b2\u00b3]', base):
            raise ValueError('文件名不能使用系统保留名称。')
        suffix = session.path.suffix if session.path else '.' + session.kind if session.kind in ('docx', 'xlsx') else '.nvdiagram' if session.kind == 'diagram' else '.' + TEXT_TYPES[session.document_format].suffixes[0]
        requested_suffix = Path(name).suffix
        if not requested_suffix:
            name += suffix
        elif requested_suffix.casefold() != suffix.casefold():
            raise ValueError('重命名不能更改文件后缀或文档类型，请使用“另存为”。')
        else:
            name = name[:-len(requested_suffix)] + suffix
        if len(name.encode('utf-16-le')) // 2 > 255:
            raise ValueError('文件名过长，请缩短后重试。')
        return name

    def rename_document_session(self, session, name) -> bool:
        if not isinstance(session, DocumentSession) or not any(item is session for item in self.sessions):
            return False
        try:
            name = self._document_rename_name(session, name)
            if name == session.name:
                return True
            source = session.path
            if source is None:
                if any(item is not session and item.path is None and item.name.casefold() == name.casefold()
                       for item in self.sessions):
                    raise ValueError('该名称已被其他打开的草稿使用，请选择其他文件名。')
                session.display_name = name
            else:
                target = source.with_name(name)
                if any(item is not session and item.path is not None and
                       item.path.resolve() == target.resolve() for item in self.sessions):
                    raise ValueError('目标文件已打开（位于其他页签），请选择其他文件名。')
                if target != source and (target.exists() or target.is_symlink()):
                    raise ValueError('目标文件或目录已存在，不能覆盖，请选择其他文件名。')
                # Windows 的 rename 在目标已存在时失败，避免检查后的竞争覆盖。
                active_preview = session is self.current_session and session.kind in ('image', 'pdf', 'office_pdf', 'ebook')
                if active_preview:
                    if session.kind == 'ebook' and session.ebook_widget is not None:
                        session.reader_state = session.ebook_widget.state()
                    else:
                        session.preview_state = self.binary_preview.state()
                        self.binary_preview.release()
                try:
                    source.rename(target)
                except OSError:
                    if active_preview:
                        self._load_session_preview(session)
                    raise
                if session.kind == 'diagram':
                    models = [session.diagram.model]
                    for command_index in range(session.diagram.undo_stack.count()):
                        command = session.diagram.undo_stack.command(command_index)
                        models.extend([command.before, command.after])
                    for model in models:
                        paths = model.get('protected_paths', [])
                        if any(Path(p).resolve() == source.resolve() for p in paths) and str(target.resolve()) not in paths:
                            paths.append(str(target.resolve()))
                session.path = target
                if session is self.current_session:
                    self.path = target
                if active_preview:
                    self._load_session_preview(session)
                self.recent = [value for value in self.recent if Path(value) != source]
                self.add_recent(target)
        except (OSError, ValueError, UnicodeError) as error:
            QMessageBox.warning(self, '重命名失败', chinese_error(error))
            return False
        self._refresh_document_tab(session)
        self.update_title()
        self.refresh_file_list()
        self.update_status()
        self.schedule_workspace_save()
        self.statusBar().showMessage('文档已重命名：' + session.name, 3000)
        return True

    def schedule_workspace_save(self, *_):
        if self.workspace is not None:
            self.workspace.schedule()

    def flush_workspace(self):
        return self.workspace.flush() if self.workspace is not None else True

    def create_category(self, name):
        name = name.strip()
        if not name or name == self.default_category_name or any(category['name'] == name for category in self.categories):
            self.statusBar().showMessage('分类名称不能为空或重复', 3000)
            return None
        category = {'id': uuid4().hex, 'name': name, 'expanded': True}
        self.categories.append(category)
        self.refresh_file_list()
        self.schedule_workspace_save()
        return category['id']

    def rename_category(self, category_id, name):
        name = name.strip()
        if not name or (category_id != '' and name == self.default_category_name) or any(c['id'] != category_id and c['name'] == name for c in self.categories):
            self.statusBar().showMessage('分类名称不能为空或重复', 3000)
            return False
        if category_id == '':
            self.default_category_name = name
            self.settings.setValue('default_category_name', name)
            self.refresh_file_list()
            return True
        for category in self.categories:
            if category['id'] == category_id:
                category['name'] = name
                self.refresh_file_list()
                self.schedule_workspace_save()
                return True
        return False

    def delete_category(self, category_id):
        if not category_id:
            self.statusBar().showMessage('默认分类必须保留，可双击修改名称。', 3000)
            return False
        self.categories[:] = [category for category in self.categories if category['id'] != category_id]
        for session in self.sessions:
            if session.category_id == category_id:
                session.category_id = ''
        self.refresh_file_list()
        self.schedule_workspace_save()

    def move_sessions_to_category(self, sessions, category_id):
        if category_id and not any(category['id'] == category_id for category in self.categories):
            return False
        for session in sessions:
            if session in self.sessions:
                session.category_id = category_id or ''
        self.refresh_file_list()
        self.schedule_workspace_save()
        return True

    def remove_sessions(self, sessions):
        targets = list({session.id: session for session in sessions if session in self.sessions}.values())
        if not targets:
            return True
        if any(session.document.isModified() for session in targets):
            answer = QMessageBox.warning(self, '移除文档',
                                        '选中的文档包含未保存的修改。移除后将清除这些文档的工作区缓存，磁盘文件不会被删除。是否继续？',
                                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return False
        if not self.workspace.flush(exclude_ids={session.id for session in targets}):
            return False
        for session in targets:
            index = self._session_tab_index(session)
            self.sessions.remove(session)
            self.document_tabs.removeTab(index)
            if not self.sessions:
                self._clear_active_document()
            self._release_diagram(session)
            self._release_image(session)
            self._release_ebook(session)
            session.document.deleteLater()
        self.refresh_file_list()
        self.schedule_workspace_save()
        return True

    def _release_diagram(self, session):
        if session.diagram is not None:
            self.document_area.removeWidget(session.diagram)
            session.diagram.deleteLater()
            session.diagram = None

    def _clear_active_document(self):
        if self.current_session is None:
            return
        if self._bound_document is not None:
            try:
                self._bound_document.modificationChanged.disconnect(self.update_title)
                self._bound_document.undoAvailable.disconnect(self.undo_action.setEnabled)
                self._bound_document.redoAvailable.disconnect(self.redo_action.setEnabled)
            except RuntimeError:
                pass
        self.current_session = None
        self._bound_document = None
        self.path = None
        self.new_document_format = 'md'
        self.is_plain_text = False
        self.undo_action.setEnabled(False)
        self.redo_action.setEnabled(False)
        document = self._new_source_document()
        document.setParent(None)
        self._suspend_document_events = True
        try:
            self.editor.setReadOnly(False)
            self.editor.set_source_document(document, temporary=True)
            self.binary_preview.release()
        finally:
            self._suspend_document_events = False
        self.update_document_mode()
        self.update_title()
        self.update_outline()
        self.update_statistics()
        self.update_status()
        self.scroll_position = 0
        self._preview_target = None
        self._schedule_preview()

    def close_document_tab(self, index):
        if index < 0 or index >= self.document_tabs.count():
            return False
        session = self.document_tabs.tabData(index)
        previous = self.document_tabs.currentIndex()
        if session.document.isModified():
            self.document_tabs.setCurrentIndex(index)
            if not self.confirm_discard():
                if previous >= 0 and previous < self.document_tabs.count():
                    self.document_tabs.setCurrentIndex(previous)
                return False
        if session in self.sessions:
            self.sessions.remove(session)
        self.document_tabs.removeTab(index)
        cache = getattr(self, '_preview_cache', {})
        removed = [key for key in cache if key[0] == id(session)]
        for key in removed:
            del cache[key]
        self.preview.page().runJavaScript(
            'for (const key of ' + json.dumps([repr(key) for key in removed])
            + ') globalThis.nvDocumentCache?.delete(key);', QWebEngineScript.ApplicationWorld)
        if self.document_tabs.count() == 0:
            self._clear_active_document()
        self._release_diagram(session)
        self._release_image(session)
        self._release_ebook(session)
        session.document.deleteLater()
        self.refresh_file_list()
        self.schedule_workspace_save()
        return True

    def base_url(self):
        directory = self.path.parent if self.path else ASSETS
        return QUrl.fromLocalFile(str(directory) + '/').toString()

    def render_preview(self):
        self.timer.stop()
        if self.is_readonly_preview:
            self._preview_generation += 1
            self._pending_preview = None
            return
        if self._preview_target is None:
            self._preview_target = ('position', self.scroll_position)
        self._preview_ready = False
        self._preview_content_key = (self._preview_cache_key(self.current_session)
                                     if self.current_session is not None else None)
        self._preview_generation += 1
        source = self.editor.source_text()
        if self.editor.length() > 16 * 1024:
            self._pending_preview = {'source': source, 'language': self.new_document_format,
                                     'theme': self.theme, 'generation': self._preview_generation,
                                     'done': Event(), 'html': None}
            if self._preview_work is None:
                self._start_preview_work()
            return
        self._pending_preview = None
        self._publish_preview(self.preview_html(source), self._preview_generation)

    def _publish_preview(self, html, generation):
        if generation != self._preview_generation:
            return
        marker = f' data-preview-generation="{generation}"'
        html, count = re.subn(r'<html\b', lambda match: match.group() + marker, html, count=1, flags=re.I)
        if not count:
            html = '<!doctype html><html' + marker + '><head></head><body>' + html + '</body></html>'
        if self.new_document_format != 'html':
            if not re.search(r'<base\b', html, re.I):
                base = '<base href="' + escape(self.base_url(), quote=True) + '">'
                html = re.sub(r'<head\b[^>]*>', lambda match: match.group() + base,
                              html, count=1, flags=re.I)
            content_key = getattr(self, '_preview_content_key', None)
            cache_key = str(content_key[0]) if content_key is not None else ''
            marker = ' data-document-cache-key="' + escape(cache_key, quote=True) + '"'
            html = html.replace('<html ', '<html' + marker + ' ', 1)
            script = '''((html) => {
                const next = new DOMParser().parseFromString(html, 'text/html');
                const root = document.documentElement;
                if (!root.dataset.documentCacheKey) return false;
                if (root.dataset.documentCacheKey !== next.documentElement.dataset.documentCacheKey ||
                        !root.dataset.codePreview || root.dataset.codePreview !==
                        next.documentElement.dataset.codePreview) {
                    getSelection().removeAllRanges();
                    delete globalThis.nvSelection;
                    delete globalThis.nvScrollMap;
                    document.head.replaceChildren(...next.head.childNodes);
                    document.body.replaceChildren(...next.body.childNodes);
                    for (const attribute of [...root.attributes]) root.removeAttribute(attribute.name);
                    for (const attribute of [...next.documentElement.attributes])
                        root.setAttribute(attribute.name, attribute.value);
                    return true;
                }
                globalThis.nvSyntaxObserver?.disconnect();
                for (const key of globalThis.nvSyntaxKeys || []) CSS.highlights.delete(key);
                if (globalThis.nvPrepareCode)
                    globalThis.nvPrepareCode(next);
                getSelection().removeAllRanges();
                // Release maps holding old DOM nodes before updating code blocks.
                delete globalThis.nvSelection;
                delete globalThis.nvScrollMap;
                const oldBlocks = [...document.body.children];
                const newBlocks = [...next.body.children];
                const reusable = new Map();
                for (const block of oldBlocks) {
                    const key = block.nvOriginalHTML ?? block.innerHTML;
                    if (!reusable.has(key)) reusable.set(key, []);
                    reusable.get(key).push(block);
                }
                const desired = newBlocks.map(block => {
                    const candidates = reusable.get(block.nvOriginalHTML ?? block.innerHTML);
                    const current = candidates?.shift() || block;
                    if (block.hasAttribute('data-source-line'))
                        current.dataset.sourceLine = block.dataset.sourceLine;
                    return current;
                });
                const kept = new Set(desired);
                for (const block of oldBlocks) if (!kept.has(block)) block.remove();
                let cursor = document.body.firstElementChild;
                for (const block of desired) {
                    if (block === cursor) cursor = cursor.nextElementSibling;
                    else document.body.insertBefore(block, cursor);
                }
                root.dataset.previewGeneration = next.documentElement.dataset.previewGeneration;
                return true;
            })(''' + json.dumps(html) + ')'

            def updated(success):
                if generation != self._preview_generation:
                    return
                if success:
                    self._preview_loaded(True)
                else:
                    self._load_preview_html(html, generation)

            self.preview.page().runJavaScript(script, QWebEngineScript.ApplicationWorld, updated)
        else:
            self._load_preview_html(html, generation)

    def _load_preview_html(self, html, generation):
        if generation != self._preview_generation:
            return
        if len(html.encode('utf-8')) > 512 * 1024 and self._preview_directory.isValid():
            if not re.search(r'<base\b', html, re.I):
                base = '<base href="' + escape(self.base_url(), quote=True) + '">'
                html, count = re.subn(r'<head\b[^>]*>', lambda match: match.group() + base,
                                     html, count=1, flags=re.I)
                if not count:
                    html = re.sub(r'<html\b[^>]*>', lambda match: match.group() + '<head>' + base + '</head>',
                                  html, count=1, flags=re.I)
            path = Path(self._preview_directory.path()) / f'preview-{generation}.html'
            path.write_text(html, encoding='utf-8')
            self.preview.load(QUrl.fromLocalFile(str(path)))
        else:
            self.preview.setHtml(html, QUrl(self.base_url()))

    def _start_preview_work(self):
        work = self._pending_preview
        self._pending_preview = None
        self._preview_work = work
        self.preview_work_timer.start()
        Thread(target=self._render_preview_work, args=(work,), daemon=True).start()

    @staticmethod
    def _render_preview_work(work):
        try:
            renderer = {'md': render_html, 'txt': render_plain_text}.get(work['language'])
            work['html'] = (work['source'] if work['language'] == 'html' else
                            renderer(work['source'], work['theme']) if renderer else
                            render_code_preview(work['source'], work['language'], work['theme']))
        except Exception:
            logging.getLogger(__name__).exception('Preview rendering failed')
        finally:
            work['done'].set()

    def _poll_preview_work(self):
        work = self._preview_work
        if work is None or not work['done'].is_set():
            return
        self.preview_work_timer.stop()
        self._preview_work = None
        if work['html'] is not None:
            self._publish_preview(work['html'], work['generation'])
        if self._pending_preview is not None:
            self._start_preview_work()

    def _schedule_preview(self):
        if self.editor.length() > MAX_PREVIEW_HIGHLIGHT_BYTES:
            self.timer.start()
        else:
            self.render_preview()

    def preview_html(self, source, base_url=''):
        language = self.new_document_format
        if language == 'md':
            return render_html(source, self.theme, base_url)
        if language == 'txt':
            return render_plain_text(source, self.theme)
        if language == 'html':
            return source
        return render_code_preview(source, language, self.theme)

    def scroll_editor_top(self, _checked=False):
        self.editor.scroll_to_top()
        self._navigate_preview(('top', 0))

    def scroll_editor_bottom(self, _checked=False):
        self.editor.scroll_to_bottom()
        self._navigate_preview(('bottom', 0))

    def scroll_preview_top(self, _checked=False):
        self.scroll_editor_top()

    def scroll_preview_bottom(self, _checked=False):
        self.scroll_editor_bottom()

    def _navigate_preview(self, target):
        self._preview_target = target
        if self.timer.isActive():
            self.render_preview()
        elif self._preview_ready:
            self._apply_preview_navigation()

    def _apply_preview_navigation(self):
        target = self._preview_target
        if target is None:
            return
        generation = self._preview_generation
        kind, value = target
        if kind == 'heading':
            script = (f'const heading = document.getElementById("heading-{int(value)}");'
                      'if (!heading) return false;'
                      'window.scrollTo(0, scrollY + heading.getBoundingClientRect().top - 16);')
        elif kind == 'bottom':
            script = 'window.scrollTo(0, document.documentElement.scrollHeight);'
        else:
            script = f'window.scrollTo(0, {float(value)});'

        def navigated(_success):
            if generation == self._preview_generation and self._preview_target == target:
                self._preview_target = None

        # 导航脚本在隔离环境中执行，不依赖页面是否允许 JavaScript。
        script = (f'(() => {{ if (document.documentElement.dataset.previewGeneration !== "{generation}") '
                  'return false;' + script + 'return true; })()')
        self.preview.page().runJavaScript(script, QWebEngineScript.ApplicationWorld, navigated)

    def _preview_loaded(self, success):
        if not success:
            return
        generation = self._preview_generation

        def ready(loaded_generation):
            if generation == self._preview_generation and loaded_generation == str(generation):
                self._preview_ready = True
                self.scroll_sync.loaded()
                self.selection_sync.loaded()
                self._apply_preview_navigation()
                if self._preview_directory.isValid():
                    current_file = Path(self.preview.url().toLocalFile())
                    for path in Path(self._preview_directory.path()).glob('preview-*.html'):
                        if path != current_file:
                            path.unlink(missing_ok=True)

        self.preview.page().runJavaScript(COMPACT_HIGHLIGHT_SCRIPT +
                                         ';document.documentElement.dataset.previewGeneration',
                                         QWebEngineScript.ApplicationWorld, ready)

    def update_encoding_menu(self):
        session = self.current_session
        text = session is not None and session.kind == 'text'
        for action in self.encoding_menu.actions():
            if action.isSeparator():
                continue
            convert, encoding = action.data()
            action.setEnabled(text and (convert or (session.path is not None and session.path.is_file())))
            action.setCheckable(True)
            action.setChecked(text and session.encoding == encoding)

    def convert_encoding(self, encoding):
        session = self.current_session
        if session is None or session.kind != 'text' or encoding not in ENCODING_LABELS:
            return False
        try:
            encode_text(self.editor.source_text(), encoding)
        except (UnicodeError, LookupError) as error:
            QMessageBox.critical(self, '编码转换失败', '当前文字无法完整转换为所选编码。\n\n' + str(error))
            return False
        if session.encoding != encoding:
            session.encoding = encoding
            session.document.set_encoding_modified()
            self.update_status()
            self.schedule_workspace_save()
        return True

    def reload_encoding(self, encoding):
        session = self.current_session
        if (session is None or session.kind != 'text' or session.path is None
                or not session.path.is_file() or encoding not in ENCODING_LABELS):
            return False
        try:
            text, encoding, newline = load_text_document(session.path, encoding=encoding)
        except (OSError, ValueError, LookupError) as error:
            QMessageBox.critical(self, '重新读取失败', '无法按所选编码完整读取文件。\n\n' + str(error))
            return False
        if session.document.isModified():
            answer = QMessageBox.question(
                self, '重新读取编码', '按所选编码重新读取磁盘内容？未保存的文字及编码修改将丢失。',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return False
        selection = self.editor.selection_state()
        first_line = self.editor.firstVisibleLine()
        self._suspend_document_events = True
        try:
            session.document.setPlainText(text)
            session.document.setModified(False)
            session.encoding, session.newline = encoding, newline
            self.editor.restore_selection_state(selection)
            self.editor.setFirstVisibleLine(first_line)
        finally:
            self._suspend_document_events = False
        self.update_title()
        self.update_statistics()
        self.update_outline()
        self._schedule_preview()
        self.schedule_workspace_save()
        return True

    def update_status(self):
        session = self.current_session
        encoding = session.encoding if session else 'utf-8'
        text = session is not None and session.kind == 'text'
        self.encoding_label.setEnabled(text)
        self.encoding_label.setText('  ' + (ENCODING_LABELS.get(encoding, encoding.upper()) if text else '只读' if session else 'UTF-8') + '  ')
        if self.is_readonly_preview:
            self.stats_label.setText('只读预览  ·  ' + self.current_session.kind.upper())
            return
        line, column = self.editor.cursor_position()
        self.stats_label.setText(f'{self._stats_count} 字词  ·  {self._stats_char_count} 字符    行 {line}，列 {column}  ')

    def update_statistics(self):
        self.stats_timer.stop()
        document = self.editor.document()
        if document.statistics_revision != document.revision:
            if self.editor.length() > MAX_PREVIEW_HIGHLIGHT_BYTES:
                key = (document, document.revision)
                if self._stats_work is None or self._stats_work['key'] != key:
                    self._pending_stats = dict(key=key, source=self.editor.source_text(),
                                               done=Event(), statistics=None)
                    if self._stats_work is None:
                        self._start_statistics_work()
            else:
                text = self.editor.source_text()
                document.statistics = (sum(1 for _ in re.finditer(r'[\u4e00-\u9fff]|[A-Za-z0-9_]+', text)), len(text))
                document.statistics_revision = document.revision
        self._stats_count, self._stats_char_count = document.statistics
        self.update_status()

    def _start_statistics_work(self):
        self._stats_work, self._pending_stats = self._pending_stats, None
        self.stats_work_timer.start()
        Thread(target=self._count_statistics, args=(self._stats_work,), daemon=True).start()

    @staticmethod
    def _count_statistics(work):
        try:
            text = work['source']
            work['statistics'] = (sum(1 for _ in re.finditer(r'[\u4e00-\u9fff]|[A-Za-z0-9_]+', text)), len(text))
        finally:
            work['done'].set()

    def _poll_statistics_work(self):
        work = self._stats_work
        if work is None or not work['done'].is_set():
            return
        self.stats_work_timer.stop()
        self._stats_work = None
        document, revision = work['key']
        if document.revision == revision and work['statistics'] is not None:
            document.statistics = work['statistics']
            document.statistics_revision = revision
            if document is self.editor.document():
                self._stats_count, self._stats_char_count = document.statistics
                self.update_status()
        if self._pending_stats is not None:
            self._start_statistics_work()

    def update_title(self, *_):
        self.rename_action.setEnabled(self.current_session is not None)
        name = self.current_session.name if self.current_session else '未命名.' + self.new_document_format
        modified = self.editor.document().isModified()
        key = (self.current_session.id if self.current_session else None, self.path,
               self.new_document_format, modified, name)
        if key == getattr(self, '_title_key', None):
            return
        self._title_key = key
        dirty = ' •' if modified else ''
        self.setWindowTitle(f'{name}{dirty} - Notepad View' if self.current_session else 'Notepad View')
        for session in self._tab_sessions():
            self._refresh_document_tab(session)
        self.side_filename.setText(name if self.current_session else '')
        self.path_label.setText((('已修改  ·  ' if modified else '') + (str(self.path) if self.path else name))
                                if self.current_session else '')
        self.path_label.setToolTip(str(self.path or '尚未保存') if self.current_session else '')
        self.refresh_file_list()

    def update_outline(self):
        self.outline_timer.stop()
        key = (self.editor.document(), self.editor.document().revision, self.new_document_format)
        if key == self._outline_key:
            return
        self._outline_key = key
        self.outline.clear()
        if self.new_document_format != 'md':
            return
        source = self.editor.source_text()
        if self.editor.length() > 16 * 1024:
            self._pending_outline = dict(key=key, source=source, headings=None, done=Event())
            if self._outline_work is None:
                self._start_outline_work()
            return
        self._pending_outline = None
        self._show_headings(markdown_headings(source))

    def _show_headings(self, headings):
        for line, level, title in headings:
            item = QListWidgetItem('  ' * (level - 1) + title)
            item.setData(Qt.UserRole, line)
            item.setToolTip(title)
            self.outline.addItem(item)

    def _start_outline_work(self):
        self._outline_work, self._pending_outline = self._pending_outline, None
        self.outline_work_timer.start()
        Thread(target=self._parse_outline_work, args=(self._outline_work,), daemon=True).start()

    @staticmethod
    def _parse_outline_work(work):
        try:
            work['headings'] = markdown_headings(work['source'])
        except Exception:
            logging.getLogger(__name__).exception('Outline parsing failed')
        finally:
            work['done'].set()

    def _poll_outline_work(self):
        work = self._outline_work
        if work is None or not work['done'].is_set():
            return
        self.outline_work_timer.stop()
        self._outline_work = None
        key = (self.editor.document(), self.editor.document().revision, self.new_document_format)
        if work['key'] == key and work['headings'] is not None:
            self.outline.clear()
            self._show_headings(work['headings'])
        if self._pending_outline is not None:
            self._start_outline_work()

    def jump_heading(self, item):
        if item is None:
            return
        line = item.data(Qt.UserRole)
        self.editor.goto_line(line)
        self._navigate_preview(('heading', line))
        if self.mode != 'preview':
            self.editor.setFocus()

    def confirm_discard(self):
        if not (self.current_session.document if self.current_session else self.editor.document()).isModified():
            return True
        answer = QMessageBox.warning(self, '保存更改', '当前文档有尚未保存的更改。是否保存？',
                                     QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        if answer == QMessageBox.Save:
            return self.save_document()
        return answer == QMessageBox.Discard

    def choose_new_document(self, _checked=False, *, category_id=None):
        dialog = NewDocumentDialog(self)
        try:
            if dialog.exec() == QDialog.Accepted:
                return self.new_document(dialog.selected_format(), category_id=category_id)
            return False
        finally:
            dialog.deleteLater()

    def new_document(self, document_format='md', category_id=None):
        if document_format in ('docx', 'xlsx') or is_office_reader('document.' + document_format):
            return False
        untyped = document_format == 'untyped'
        document_format = document_format if document_format in TEXT_TYPES else 'txt'
        category_id = self.file_list.current_category() if category_id is None else category_id
        if not isinstance(category_id, str) or not any(item['id'] == category_id for item in self.categories):
            category_id = ''
        document = self._new_source_document()
        session = DocumentSession(document=document, document_format=document_format,
                                  manual_format=not untyped, category_id=category_id, untyped=untyped)
        self.sessions.append(session)
        index = self.document_tabs.addTab(session.name)
        self.document_tabs.setTabData(index, session)
        self.document_tabs.setCurrentIndex(index)
        if self.current_session is not session:
            self._bind_document(session)
        self.editor.setFocus()
        return True

    def open_document(self):
        path, _ = QFileDialog.getOpenFileName(self, '打开文档', str(self.path.parent if self.path else Path.home()),
                                             '所有文件 (*);;' + ';;'.join(save_filter(key) for key in TEXT_TYPES) + ';;图片 (*.png *.jpg *.jpeg *.gif *.webp *.bmp *.tif *.tiff *.svg);;PDF (*.pdf);;Office 文档 (*.doc *.docx *.xls *.xlsx *.ppt *.pptx *.odt *.ods *.odp *.rtf);;电子书 (*.epub *.mobi *.azw3 *.fb2)',
                                             options=QFileDialog.DontUseNativeDialog)
        if path:
            self.open_path_guarded(Path(path))

    def open_path_guarded(self, path):
        return self.open_document_path(path)

    def load_path(self, path):
        return self.open_document_path(path)

    def open_document_path(self, path):
        path = Path(path).resolve()
        existing = next((s for s in self.sessions if s.path == path), None)
        if existing is not None:
            answer = QMessageBox.question(
                self, '文件已打开',
                f'“{path.name}”已打开，是否用磁盘内容覆盖当前已打开的内容？\n未保存的修改将丢失。',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return False
        try:
            from notepadview.diagrams import SUFFIXES, read_diagram
            ebook_suffix = path.suffix.casefold()
            kind = ('ebook' if ebook_suffix == '.epub' or ebook_suffix in EBOOK_CONVERT_SUFFIXES else
                    'office_pdf' if is_office_reader(path) else
                    path.suffix.lower()[1:] if path.suffix.lower() in ('.docx', '.xlsx') else
                    'diagram' if path.suffix.lower() in SUFFIXES else
                    'image' if path.suffix.lower() == '.svg' else file_kind(path))
            diagram_model = read_diagram(path) if kind == 'diagram' else None
            image_editor = None
            if kind in ('docx', 'xlsx'):
                from notepadview.office_editor import OfficeEditor
                image_editor = OfficeEditor.from_path(path, self)
            if kind == 'image':
                from PySide6.QtGui import QImageReader
                from notepadview.image_editor import ImageEditor
                reader = QImageReader(str(path))
                reader.setDecideFormatFromContent(True)
                if path.suffix.lower() == '.svg' or (path.suffix.lower() in ('.png', '.jpg', '.jpeg')
                        and bytes(reader.format()).lower() in (b'png', b'jpg', b'jpeg')):
                    image_editor = ImageEditor.from_path(path, self)
            text, encoding, newline = load_text_document(path) if kind == 'text' else ('', 'utf-8', '\n')
            if kind in ('image', 'pdf') and image_editor is None:
                # 先验证解码成功，再替换现有页签，损坏文件不丢失已有内容。
                from notepadview.binary_preview import BinaryPreview
                probe = BinaryPreview(self)
                try:
                    probe.load(path, kind)
                finally:
                    probe.release()
                    probe.deleteLater()
        except BinaryDocumentError:
            QMessageBox.warning(self, '不支持的文件',
                f'“{path.name}”不是可可靠识别的文本、图片、PDF 或图表。暂不支持此二进制文件。')
            return False
        except (OSError, UnicodeError, ValueError) as error:
            QMessageBox.critical(self, '无法打开文件', f'{path}\n\n{chinese_error(error)}')
            return False
        if existing is None:
            session = DocumentSession(document=self._new_source_document(text), path=path)
            self.sessions.append(session)
            self.document_tabs.blockSignals(True)
            index = self.document_tabs.addTab(path.name)
            self.document_tabs.setTabData(index, session)
            self.document_tabs.blockSignals(False)
        else:
            session = existing
            index = self._session_tab_index(session)
            if session is self.current_session:
                self.editor.setReadOnly(False)
            session.document.setPlainText(text)
            session.document.setModified(False)
        self._release_image(session)
        self._release_diagram(session)
        self._release_ebook(session)
        session.kind, session.encoding, session.newline = kind, encoding, newline
        if kind in ('office_pdf', 'ebook'):
            session.reader_generation += 1
            session.render_path = None
            session.reader_error = None
            session.reader_state = {}
        if image_editor is not None:
            self._attach_image(session, image_editor)
        session.refresh_text_format(text)
        if kind != 'text':
            session.document_format = 'txt'
        session.preview_state = {}
        if kind == 'diagram':
            self._attach_diagram(session, diagram_model)
            if diagram_model['warnings']:
                QMessageBox.warning(self, '图形导入提示', '\n'.join(diagram_model['warnings']) + '\n部分内容无法完整保留。为保护原文件，请另存为新文件。')
        self.document_tabs.blockSignals(True)
        self.document_tabs.setCurrentIndex(index)
        self.document_tabs.blockSignals(False)
        self._bind_document(session)
        self.add_recent(path)
        return True

    def save_to(self, path, *, session=None):
        target = self.current_session if session is None else session
        if target is None:
            self._adopt_current_document()
            target = self.current_session
        if not any(item is target for item in self.sessions):
            return False
        if target.kind in ('docx', 'xlsx'):
            return False
        if target.image_editor is not None:
            return self._save_image_to(path, target)
        if target.kind not in ('text', 'diagram'):
            return False
        if target.kind == 'diagram':
            return self._save_diagram_to(path, session=target)
        destination = Path(path).resolve()
        if any(item is not target and item.path == destination for item in self.sessions):
            QMessageBox.warning(self, '文件已打开', '目标文件已在其他页签中打开，请切换到该页签保存，或选择其他文件名。')
            return False
        source = bytes(target.document.get_char_range(0, target.document.length())).decode('utf-8')
        try:
            save_document(destination, source, target.encoding, target.newline)
        except (OSError, UnicodeError) as error:
            QMessageBox.critical(self, '保存失败', chinese_error(error))
            return False
        target.path = destination
        target.untyped = False
        target.refresh_text_format(source)
        target.document.setModified(False)
        if target is self.current_session:
            self.path = destination
            self.new_document_format = target.document_format
            self.update_document_mode()
            self.update_outline()
            self._schedule_preview()
        self.add_recent(destination)
        self.update_title()
        self.schedule_workspace_save()
        self.statusBar().showMessage('文档已保存', 2500)
        return True

    def _save_image_to(self, path, target):
        destination = Path(path).resolve()
        if any(item is not target and item.path == destination for item in self.sessions):
            QMessageBox.warning(self, '文件已打开', '目标文件已在其他页签中打开，请选择其他文件名。')
            return False
        try:
            target.image_editor.save(destination)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, '保存失败', chinese_error(error))
            return False
        target.path = destination
        target.image_editor.undo_stack.setClean()
        target.document.setModified(False)
        if target is self.current_session:
            self.path = destination
        self.add_recent(destination)
        self.update_title()
        self.schedule_workspace_save()
        self.statusBar().showMessage('文档已保存' if target.kind in ('docx', 'xlsx') else '图片已保存', 2500)
        return True

    def _save_diagram_to(self, path, *, session=None):
        from notepadview.diagrams import write_diagram
        target = self.current_session if session is None else session
        destination = Path(path).resolve()
        if any(s is not target and s.path == destination for s in self.sessions):
            QMessageBox.warning(self, '文件已打开', '目标文件已在其他页签中打开，请选择其他文件名。')
            return False
        model = target.diagram.model
        if model.get('warnings') and destination != target.path:
            answer = QMessageBox.question(self, '保存图表', '此图表包含兼容性提示，是否另存为？', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return False
        try:
            write_diagram(destination, model)
        except (OSError, ValueError, RecursionError) as error:
            QMessageBox.warning(self, '保存失败', str(error))
            return False
        target.path = destination
        if target is self.current_session:
            self.path = destination
        target.diagram.undo_stack.setClean()
        target.document.setModified(False)
        self.add_recent(destination)
        self.update_title()
        self.schedule_workspace_save()
        return True

    def run_script(self):
        session = self.current_session
        if session is None or session.kind != 'text' or session.document_format not in ('python', 'powershell'):
            return False
        language = session.document_format
        if not self.save_document():
            return False
        try:
            script_runner.launch_script(session.path, language)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, '运行失败', str(error))
            return False
        return True

    def save_document(self):
        if self.current_session and self.current_session.image_editor is not None:
            return self.save_to(self.path) if self.path else self.save_as()
        if self.is_diagram:
            protected = self.current_session.diagram.model.get('protected_paths', [])
            return self.save_as() if not self.path or any(self.path.resolve() == Path(p).resolve() for p in protected) else self.save_to(self.path)
        if self.is_readonly_preview:
            return False
        return self.save_to(self.path) if self.path else self.save_as()

    def save_as(self, *, session=None):
        target = self.current_session if session is None else session
        if target is not None and target.kind in ('docx', 'xlsx'):
            return False
        if target is not None and target.image_editor is not None:
            filters = ('SVG (*.svg)' if target.image_editor.image_format == 'svg'
                       else 'PNG (*.png);;JPEG (*.jpg *.jpeg)')
            path, selected = QFileDialog.getSaveFileName(self, '另存为图片', str(target.path), filters,
                                                        options=QFileDialog.DontUseNativeDialog)
            if not path:
                return False
            if not Path(path).suffix:
                path += '.svg' if target.image_editor.image_format == 'svg' else '.jpg' if 'JPEG' in selected else '.png'
            return self.save_to(path, session=target)
        if target is not None and (not any(item is target for item in self.sessions)
                                   or target.kind not in ('text', 'diagram')):
            return False
        if target is not None and target.kind == 'diagram':
            path, selected = QFileDialog.getSaveFileName(self, '另存为图表', str(target.path or Path.home() / '未命名.nvdiagram'), '图表 (*.nvdiagram);;draw.io (*.drawio);;XMind (*.xmind)', options=QFileDialog.DontUseNativeDialog)
            if not path:
                return False
            if not Path(path).suffix:
                path += '.xmind' if 'xmind' in selected else '.drawio' if 'drawio' in selected else '.nvdiagram'
            return self.save_to(path, session=target)
        suffix = target.document_format if target is not None else self.new_document_format
        name = target.name if target is not None else f'未命名.{suffix}'
        path, selected_filter = QFileDialog.getSaveFileName(self, '保存文档', str(target.path if target is not None and target.path else Path.home() / name),
                                             ';;'.join(save_filter(key) for key in TEXT_TYPES) + ';;所有文件 (*)',
                                             save_filter(suffix),
                                             options=QFileDialog.DontUseNativeDialog)
        if not path:
            return False
        if target is not None and target.untyped and not Path(path).suffix:
            language = next((key for key in TEXT_TYPES if save_filter(key) == selected_filter), None)
            if language is not None:
                path += '.' + TEXT_TYPES[language].suffixes[0]
        return self.save_to(path, session=target)

    def export_to(self, path):
        if self.is_readonly_preview:
            return False
        destination = Path(path).resolve()
        if any(session.path == destination for session in self.sessions):
            QMessageBox.warning(self, '文件已打开', '导出目标已在页签中打开，请选择其他文件名。')
            return False
        try:
            source = self.editor.source_text()
            html = self.preview_html(source, self.base_url())
            save_document(Path(path), html)
        except (OSError, UnicodeError) as error:
            QMessageBox.critical(self, '导出失败', chinese_error(error))
            return False
        self.statusBar().showMessage('HTML 已导出', 3000)
        return True

    def update_document_mode(self):
        readonly = self.is_readonly_preview
        image = self.current_session.image_editor if self.current_session else None
        ebook = (self.current_session.ebook_widget if self.current_session and
                 self.current_session.kind == 'ebook' else None)
        self.document_area.setCurrentWidget(image if image is not None else
                                            self.current_session.diagram if self.is_diagram else
                                            ebook if ebook is not None else
                                            self.binary_preview if readonly else self.editor_split)
        for action in (self.save_action, self.save_as_action, self.export_action, self.find_action,
                       *self.view_actions.values()):
            action.setEnabled(not readonly)
        if self.is_diagram:
            self.save_action.setEnabled(True)
            self.save_as_action.setEnabled(True)
            self.undo_action.setEnabled(self.current_session.diagram.undo_stack.canUndo())
            self.redo_action.setEnabled(self.current_session.diagram.undo_stack.canRedo())
        if image is not None and self.current_session.kind in ('docx', 'xlsx'):
            self.save_action.setEnabled(False)
            self.save_as_action.setEnabled(False)
            self.undo_action.setEnabled(False)
            self.redo_action.setEnabled(False)
        elif image is not None:
            self.save_action.setEnabled(True)
            self.save_as_action.setEnabled(True)
            self.undo_action.setEnabled(image.undo_stack.canUndo())
            self.redo_action.setEnabled(image.undo_stack.canRedo())
        elif readonly and not self.is_diagram:
            self.undo_action.setEnabled(False)
            self.redo_action.setEnabled(False)
        self.split_button.setEnabled(not readonly)
        self.editor.setReadOnly(readonly)
        if readonly:
            self.find_bar.hide()
            self.find_action.setChecked(False)
        language = self.new_document_format
        self.run_button.setVisible(not readonly and self.current_session is not None
                                   and language in ('python', 'powershell'))
        self.preview.settings().setAttribute(QWebEngineSettings.JavascriptEnabled,
                                             not readonly and language == 'html')
        self.is_plain_text = language == 'txt'
        if self.editor.language != language:
            self.editor.set_language(language)
        for action in self.format_actions:
            action.setEnabled(not readonly and language == 'md')
        self.format_document_action.setEnabled(not readonly and TEXT_TYPES[language].format_enabled)
        self.source_footer.setText('  ' + TEXT_TYPES[language].label.upper())
        self.language_button.setText('语言：' + TEXT_TYPES[language].label)
        self.language_button.setEnabled(not readonly)
        for key, action in self.type_actions.items():
            action.setEnabled(not readonly)
            action.setChecked(key == language)

    def change_document_language(self, language):
        if self.is_readonly_preview or language not in TEXT_TYPES:
            return False
        if self.current_session is None:
            self._adopt_current_document()
        session = self.current_session
        if session.kind != 'text':
            return False
        suffix = '.' + TEXT_TYPES[language].suffixes[0]
        try:
            if session.path is not None:
                source = session.path
                target = source.with_suffix(suffix)
                if source.suffix.casefold() != suffix:
                    if any(item is not session and item.path is not None and
                           item.path.resolve() == target.resolve() for item in self.sessions):
                        raise ValueError('目标文件已在其他页签中打开，无法切换类型。')
                    if target.exists() or target.is_symlink():
                        raise ValueError('目标文件已存在，无法切换类型，避免覆盖已有文件。')
                    source.rename(target)
                    session.path = target
                    self.path = target
                    self.recent = [value for value in self.recent if Path(value) != source]
                    self.add_recent(target)
                session.format_suffix = session.path.suffix.casefold()
            elif session.display_name:
                name = str(Path(session.display_name).with_suffix(suffix))
                if any(item is not session and item.path is None and
                       item.name.casefold() == name.casefold() for item in self.sessions):
                    raise ValueError('该名称已被其他打开的草稿使用，无法切换类型。')
                session.display_name = name
        except (OSError, ValueError) as error:
            self.update_document_mode()
            QMessageBox.warning(self, '切换文件类型失败', chinese_error(error))
            return False
        session.untyped = False
        return self.set_document_type(language)

    def set_document_type(self, language):
        if self.is_readonly_preview or language not in TEXT_TYPES:
            return False
        if self.current_session is None:
            self._adopt_current_document()
        self.current_session.document_format = language
        self.current_session.manual_format = True
        self.new_document_format = language
        self.update_document_mode()
        self._refresh_document_tab(self.current_session)
        self.refresh_file_list()
        self.update_title()
        self.update_outline()
        self._schedule_preview()
        self.schedule_workspace_save()
        return True

    @staticmethod
    def format_for_path(path):
        return type_for_path(path)

    def format_document(self):
        if self.is_readonly_preview:
            return False
        if not TEXT_TYPES[self.new_document_format].format_enabled:
            return False
        success = self.editor.apply_format_operation('format')
        if success:
            self.render_preview()
        return success

    def export_html(self):
        if self.is_readonly_preview:
            return False
        path, _ = QFileDialog.getSaveFileName(self, '导出 HTML', str(self.path.with_suffix('.html') if self.path else Path.home() / '未命名.html'), 'HTML (*.html)',
                                             options=QFileDialog.DontUseNativeDialog)
        if path:
            self.export_to(path)

    def add_recent(self, path):
        value = str(path)
        self.recent = ([value] + [p for p in self.recent if p != value])[:12]
        self.settings.setValue('recent', self.recent)
        self.refresh_recent()

    def refresh_recent(self):
        self.recent_menu.clear()
        for path in self.recent:
            self.recent_menu.addAction(self.action(Path(path).name, lambda _=False, p=path: self.open_path_guarded(Path(p))))
        self.recent_menu.setEnabled(bool(self.recent))

    def wrap_selection(self, prefix, suffix, default):
        if self.is_readonly_preview:
            return
        if self.editor.selectionIsRectangle() or self.editor.selections() > 1:
            self.statusBar().showMessage('Markdown 格式操作需要普通文本选区', 3000)
            return
        self.set_mode('source' if self.mode == 'preview' else self.mode, remember=False)
        text = self.editor.selected_text() or default
        self.editor.beginUndoAction()
        self.editor.replace_selection(prefix + text + suffix)
        self.editor.endUndoAction()
        self.editor.setFocus()

    def insert_link(self):
        if self.is_readonly_preview:
            return
        url, ok = QInputDialog.getText(self, '插入链接', '链接地址', text='https://')
        if ok and url.strip():
            self.wrap_selection('[', '](' + url.strip() + ')', '链接文字')

    def insert_image(self):
        if self.is_readonly_preview:
            return
        path, _ = QFileDialog.getOpenFileName(self, '插入图片', str(self.path.parent if self.path else Path.home()),
                                             '图片 (*.png *.jpg *.jpeg *.gif *.webp *.bmp)',
                                             options=QFileDialog.DontUseNativeDialog)
        if not path:
            return
        import os
        url = QUrl.fromLocalFile(path).toString()
        if self.path:
            try:
                url = quote(Path(os.path.relpath(path, self.path.parent)).as_posix(), safe='/')
            except ValueError:
                pass
        self.wrap_selection('![', '](' + url.replace('(', '%28').replace(')', '%29') + ')', '图片')

    def _position_find_bar(self):
        panel = self.preview_panel if self.mode == 'preview' else self.source_panel
        if self.find_bar.parentWidget() is panel:
            return
        anchor = self.preview if panel is self.preview_panel else self.editor
        self.find_bar.attach(panel, anchor)

    def show_find(self):
        if self.is_readonly_preview:
            return
        self._position_find_bar()
        self.find_bar.show()
        self.find_action.setChecked(True)
        self.find_input.setFocus()
        self.find_input.selectAll()

    def hide_find(self):
        self.find_bar.hide()
        self.preview.findText('')
        self.find_action.setChecked(False)
        (self.preview if self.mode == 'preview' else self.editor).setFocus()

    def toggle_find(self, checked):
        focus = QApplication.focusWidget()
        preview_focused = focus is self.preview or (
            focus is not None and self.preview.isAncestorOf(focus))
        if self.mode == 'preview' or preview_focused:
            selected = self.preview.page().selectedText()
        else:
            selected = self.editor.selected_text()
        if selected:
            self.find_input.setText(selected)
            self.show_find()
        elif checked:
            self.show_find()
        else:
            self.hide_find()

    def find_next(self, _checked=False, backward=False, replaced_count=None):
        if self.is_readonly_preview:
            return False
        term = self.find_input.text()
        if not term:
            self.show_find()
            return False
        try:
            count = self._find_match_count(term)
            found = self.editor.find_text(term, backward, **self._find_options())
            if self.mode == 'preview':
                flags = QWebEnginePage.FindFlag(0)
                if backward:
                    flags |= QWebEnginePage.FindBackward
                if self.case_checkbox.isChecked():
                    flags |= QWebEnginePage.FindCaseSensitively
                self.preview.findText(
                    self.editor.selected_text() if found else '', flags)
        except SearchError as error:
            prefix = f'已替换 {replaced_count} 处；' if replaced_count is not None else ''
            self._find_message(f'{prefix}{error}')
            return False
        if replaced_count is not None:
            self._find_message(f'已替换 {replaced_count} 处；当前文档剩余匹配 {count} 处')
            return found
        if not found:
            self._find_message(f'未找到匹配内容；当前文档共 {count} 处匹配')
            return False
        self._find_message(f'当前文档共 {count} 处匹配')
        return True

    def _find_options(self):
        return dict(regular_expression=self.regex_checkbox.isChecked(),
                    case_sensitive=self.case_checkbox.isChecked(),
                    whole_word=self.whole_word_checkbox.isChecked())

    def _find_match_count(self, term):
        operation = BoundedSearch(term, **self._find_options())
        return operation.count(self.editor.source_text())

    def _find_message(self, message):
        count_color, no_match_color = _FIND_FEEDBACK_COLORS[self.theme]
        text = escape(message)
        text = _FIND_COUNT_PATTERN.sub(
            lambda match: f'<span style="color:{count_color}">{match.group()}</span>', text)
        no_match = escape('未找到匹配内容')
        text = text.replace(no_match, f'<span style="color:{no_match_color}">{no_match}</span>')
        self.find_feedback.setText(text)
        self.statusBar().showMessage(message, 3500)

    def replace_one(self):
        if self.is_readonly_preview:
            return
        term = self.find_input.text()
        if not term:
            return
        try:
            replaced = self.editor.replace_match(term, self.replace_input.text(),
                                                 **self._find_options())
        except SearchError as error:
            self._find_message(str(error))
            return
        if not replaced:
            try:
                count = self._find_match_count(term)
            except SearchError as error:
                self._find_message(str(error))
                return
            self._find_message(f'未找到匹配内容；当前文档共 {count} 处匹配')
            return
        self.find_next(replaced_count=1)

    def replace_all(self):
        if self.is_readonly_preview:
            return
        term = self.find_input.text()
        if not term:
            return
        try:
            count = self.editor.replace_all_matches(term, self.replace_input.text(),
                                                    **self._find_options())
            remaining = self._find_match_count(term)
        except SearchError as error:
            self._find_message(str(error))
            return
        self._find_message(f'已替换 {count} 处；当前文档剩余匹配 {remaining} 处')

    def closeEvent(self, event):
        if not self.flush_workspace():
            event.ignore()
            return
        if self.api_workbench is not None:
            self.api_workbench.shutdown()
        if self.api_workbench_store is not None:
            self.api_workbench_store.close()
        if self.workspace is not None:
            self.workspace.close()
        self.settings.setValue('geometry', self.saveGeometry())
        self.settings.sync()
        self.timer.stop()
        self.stats_timer.stop()
        self.stats_work_timer.stop()
        self._pending_stats = None
        self.outline_timer.stop()
        self.outline_work_timer.stop()
        self._pending_outline = None
        self.selection_sync.work_timer.stop()
        self.selection_sync.preview_timer.stop()
        self.selection_sync.timer.stop()
        self.preview_work_timer.stop()
        self._pending_preview = None
        self._preview_generation += 1
        self.binary_preview.release()
        self.editor.close()
        self.find_bar.hide()
        event.accept()
