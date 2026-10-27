"""Isolated, read-only chapter viewer for EPUB content."""
from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtWebEngineCore import (QWebEnginePage, QWebEngineProfile,
                                    QWebEngineSettings, QWebEngineUrlRequestInterceptor)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QListWidget,
                              QListWidgetItem, QPushButton, QVBoxLayout, QWidget)


class _EpubRequestInterceptor(QWebEngineUrlRequestInterceptor):
    def __init__(self, reader):
        super().__init__(reader)
        self.reader = reader

    def interceptRequest(self, info):
        if not self.reader._is_allowed_resource(info.requestUrl()):
            info.block(True)


class _EpubPage(QWebEnginePage):
    def __init__(self, profile, reader):
        super().__init__(profile, reader)
        self.reader = reader

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        if navigation_type == QWebEnginePage.NavigationTypeLinkClicked:
            if url.scheme() != 'file' or not self.reader._is_allowed_resource(url):
                return False
            path = Path(url.toLocalFile()).resolve()
            same_page = self.url().toLocalFile() == str(path)
            if same_page:
                return True
            target = self.reader._chapter_by_path.get(path)
            if target is None:
                return False
            self.reader.select_chapter(target, url.fragment())
            return False
        if url.scheme() == 'file':
            return self.reader._is_allowed_resource(url)
        return url.scheme() in ('about', 'data')


class EbookReader(QWidget):
    stateChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.book = None
        self.current_chapter = 0
        self._zoom = 1.0
        self._scroll = 0.0
        self._pending_scroll = 0.0
        self._chapter_by_path = {}
        self.profile = QWebEngineProfile(self)
        self.interceptor = _EpubRequestInterceptor(self)
        self.profile.setUrlRequestInterceptor(self.interceptor)
        self.view = QWebEngineView(self)
        self.page = _EpubPage(self.profile, self)
        self.view.setPage(self.page)
        self.view.settings().setAttribute(QWebEngineSettings.JavascriptEnabled, False)
        self.view.settings().setAttribute(
            QWebEngineSettings.LocalContentCanAccessRemoteUrls, False)
        self.view.settings().setAttribute(
            QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        self.view.page().scrollPositionChanged.connect(self._scroll_changed)
        self.view.loadFinished.connect(self._load_finished)

        self.title_label = QLabel('电子书只读预览', self)
        self.toc_list = QListWidget(self)
        self.toc_list.itemClicked.connect(self._toc_selected)
        self.previous_button = QPushButton('上一章', self)
        self.next_button = QPushButton('下一章', self)
        self.zoom_out_button = QPushButton('缩小', self)
        self.zoom_in_button = QPushButton('放大', self)
        self.find_input = QLineEdit(self)
        self.find_input.setPlaceholderText('查找本章文字')
        self.find_next_button = QPushButton('查找', self)
        self.previous_button.clicked.connect(lambda: self.select_chapter(self.current_chapter - 1))
        self.next_button.clicked.connect(lambda: self.select_chapter(self.current_chapter + 1))
        self.zoom_out_button.clicked.connect(lambda: self.set_zoom(self._zoom / 1.2))
        self.zoom_in_button.clicked.connect(lambda: self.set_zoom(self._zoom * 1.2))
        self.find_next_button.clicked.connect(self.find_text)
        self.find_input.returnPressed.connect(self.find_text)

        toolbar = QHBoxLayout()
        toolbar.addWidget(self.title_label)
        toolbar.addStretch()
        for button in (self.previous_button, self.next_button,
                       self.zoom_out_button, self.zoom_in_button):
            toolbar.addWidget(button)
        toolbar.addWidget(self.find_input)
        toolbar.addWidget(self.find_next_button)
        content = QHBoxLayout()
        content.addWidget(self.toc_list, 0)
        content.addWidget(self.view, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(toolbar)
        layout.addLayout(content, 1)

    def _is_allowed_resource(self, url):
        if url.scheme() == 'data':
            return True
        if url.scheme() != 'file' or self.book is None:
            return False
        try:
            Path(url.toLocalFile()).resolve().relative_to(self.book.content_root.resolve())
        except (OSError, ValueError):
            return False
        return True

    def load_book(self, book, state=None):
        state = state or {}
        self.book = book
        self._chapter_by_path = {
            (book.content_root / chapter.relative_path).resolve(): index
            for index, chapter in enumerate(book.chapters)
        }
        self.title_label.setText(book.title)
        self.toc_list.clear()
        for entry in book.toc:
            item = QListWidgetItem(entry.title)
            item.setData(256, (entry.chapter, entry.fragment))
            self.toc_list.addItem(item)
        chapter = state.get('chapter', 0)
        self._zoom = max(0.5, min(3.0, float(state.get('zoom', 1.0))))
        self._pending_scroll = max(0.0, float(state.get('scroll', 0.0)))
        self._scroll = self._pending_scroll
        self.select_chapter(max(0, min(len(book.chapters) - 1, int(chapter))),
                            restore_scroll=True)

    def select_chapter(self, chapter, fragment='', restore_scroll=False):
        if self.book is None or not 0 <= chapter < len(self.book.chapters):
            return False
        self.current_chapter = chapter
        document = self.book.content_root / self.book.chapters[chapter].relative_path
        self._pending_scroll = self._pending_scroll if restore_scroll else 0.0
        url = QUrl.fromLocalFile(str(document))
        if fragment:
            url.setFragment(fragment)
        self.view.setZoomFactor(self._zoom)
        self.page.setUrl(url)
        self._update_controls()
        self.stateChanged.emit()
        return True

    def _toc_selected(self, item):
        chapter, fragment = item.data(256)
        self.select_chapter(chapter, fragment)

    def _update_controls(self):
        self.previous_button.setEnabled(self.current_chapter > 0)
        self.next_button.setEnabled(
            self.book is not None and self.current_chapter < len(self.book.chapters) - 1)

    def set_zoom(self, zoom):
        self._zoom = max(0.5, min(3.0, float(zoom)))
        self.view.setZoomFactor(self._zoom)
        self.stateChanged.emit()

    def find_text(self):
        query = self.find_input.text()
        if query:
            self.page.findText(query)

    def _scroll_changed(self, point):
        self._scroll = max(0.0, point.y())
        self.stateChanged.emit()

    def _load_finished(self, succeeded):
        if not succeeded:
            self.show_error('无法显示电子书章节。')
            return
        if self._pending_scroll:
            scroll = self._pending_scroll
            self._pending_scroll = 0.0
            self.page.runJavaScript(f'window.scrollTo(0, {scroll});')

    def state(self):
        return {'chapter': self.current_chapter, 'scroll': self._scroll, 'zoom': self._zoom}

    def show_error(self, message):
        self.title_label.setText('电子书不可用：' + str(message))
