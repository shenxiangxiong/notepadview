"""Explicitly named preview actions independent of Chromium translations."""
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMenu
from PySide6.QtWebEngineCore import QWebEngineContextMenuRequest, QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView


class PreviewView(QWebEngineView):
    def build_context_menu(self, request=None):
        menu = QMenu(self)
        selected = request.selectedText() if request is not None else self.page().selectedText()
        copy = menu.addAction('复制')
        copy.setEnabled(bool(selected))
        copy.setShortcut('Ctrl+C')
        copy.setShortcutVisibleInContextMenu(True)
        copy.triggered.connect(lambda: QApplication.clipboard().setText(selected))
        select_all = menu.addAction('全选')
        select_all.setShortcut('Ctrl+A')
        select_all.setShortcutVisibleInContextMenu(True)
        select_all.triggered.connect(lambda: self.page().triggerAction(QWebEnginePage.WebAction.SelectAll))
        if request is not None:
            link = QUrl(request.linkUrl())
            if link.isValid() and not link.isEmpty():
                menu.addSeparator()
                menu.addAction('复制链接地址', lambda: QApplication.clipboard().setText(link.toString()))
                if link.scheme().lower() in ('http', 'https', 'mailto'):
                    menu.addAction('在浏览器中打开链接', lambda: QDesktopServices.openUrl(link))
            if request.mediaType() == QWebEngineContextMenuRequest.MediaType.MediaTypeImage:
                image = QUrl(request.mediaUrl())
                menu.addSeparator()
                menu.addAction('复制图片', lambda: self.page().triggerAction(
                    QWebEnginePage.WebAction.CopyImageToClipboard))
                if image.isValid() and not image.isEmpty():
                    menu.addAction('复制图片地址', lambda: QApplication.clipboard().setText(image.toString()))
        return menu

    def contextMenuEvent(self, event):
        menu = self.build_context_menu(self.lastContextMenuRequest())
        try:
            menu.exec(event.globalPos())
        finally:
            menu.deleteLater()
        event.accept()
