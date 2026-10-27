"""Capture real context menus and a localized formatting error."""
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox

from notepadview.window import MainWindow


def main():
    app = QApplication([])
    app.setStyle('Fusion')
    app.setFont(QFont('Microsoft YaHei UI', 10))
    output = Path(__file__).resolve().parents[1] / 'screenshots/chinese-interface'
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory() as directory:
        window = MainWindow(QSettings(str(Path(directory) / 'review.ini'), QSettings.IniFormat))
        window.show()
        window.new_document('md')
        window.editor.setPlainText('# 中文菜单\n\n选中文本后可以复制。\n\n[示例链接](https://example.com)')
        for theme in ('light', 'dark', 'soft'):
            window.set_theme(theme)
            for _ in range(100):
                QTest.qWait(50)
                if window._preview_ready:
                    break
            assert window._preview_ready
            window.editor.set_selection(2, 6)
            menu = window.editor.build_context_menu()
            menu.popup(window.editor.mapToGlobal(window.editor.rect().center()))
            QTest.qWait(100)
            menu.grab().save(str(output / f'{theme}-source-menu.png'))
            menu.close()
            menu.deleteLater()
            captured = []
            timer = QTimer(window)
            timer.setInterval(20)

            def capture_preview():
                popup = app.activePopupWidget()
                if isinstance(popup, QMenu):
                    popup.grab().save(str(output / f'{theme}-preview-menu.png'))
                    captured.extend(action.text() for action in popup.actions() if not action.isSeparator())
                    popup.close()

            timer.timeout.connect(capture_preview)
            timer.start()
            target = window.preview.focusProxy() or window.preview
            QTest.mouseClick(target, Qt.RightButton, pos=target.rect().center())
            QTest.qWait(250)
            timer.stop()
            assert captured and all(captured)
            window.grab().save(str(output / f'{theme}-window.png'))
        window.new_document('json')
        window.editor.setPlainText('{"名称": }')

        def capture_error():
            popup = app.activeModalWidget()
            if isinstance(popup, QMessageBox):
                popup.grab().save(str(output / 'format-error.png'))
                popup.accept()

        QTimer.singleShot(150, capture_error)
        assert not window.format_document()
        window.close()
    print('中文菜单与格式错误截图已生成：' + str(output))


if __name__ == '__main__':
    main()
