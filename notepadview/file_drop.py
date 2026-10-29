from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtWidgets import QWidget


DOCUMENT_SUFFIXES = {'.md', '.markdown', '.mdown', '.mkd', '.txt', '.json', '.xml', '.sql'}


class FileDropFilter(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        window.setAcceptDrops(True)
        self.watch(window)
        self.watch(window.find_bar)

    def watch(self, widget):
        widget.installEventFilter(self)
        for child in widget.findChildren(QWidget):
            child.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.ChildAdded:
            child = event.child()
            if isinstance(child, QWidget):
                self.watch(child)
            return False
        if event.type() not in (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop):
            return False
        if not isinstance(watched, QWidget):
            return False
        top_level = watched.window()
        if top_level is not self.window and top_level is not self.window.find_bar:
            return False
        mime = event.mimeData()
        if not mime.hasUrls():
            return False
        if not event.possibleActions() & Qt.CopyAction:
            event.ignore()
            return True
        paths = []
        for url in mime.urls():
            if url.isLocalFile():
                path = Path(url.toLocalFile())
                if path.is_file():
                    paths.append(path)
        if not paths:
            event.ignore()
            return True
        event.setDropAction(Qt.CopyAction)
        event.accept()
        if event.type() == QEvent.Drop:
            # Finish native drag handling before opening tabs or an error dialog.
            QTimer.singleShot(0, self, lambda: self.open_files(paths))
        return True

    def open_files(self, paths):
        for path in paths:
            self.window.open_document_path(path)
