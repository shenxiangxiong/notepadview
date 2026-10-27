"""Capture the actual Qt interface in all themes, without user preferences."""
import json
from pathlib import Path
from time import monotonic

from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from notepadview.dialogs import NewDocumentDialog
from notepadview.window import MainWindow


def wait_until(predicate):
    deadline = monotonic() + 12
    while monotonic() < deadline:
        if predicate():
            return
        QTest.qWait(20)
    raise AssertionError('Preview did not become ready')


def capture():
    root = Path(__file__).resolve().parents[1]
    output = root / 'screenshots' / 'interface-refresh'
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion')
    app.setFont(QFont('Microsoft YaHei UI', 10))
    settings = QSettings(str(output / 'review.ini'), QSettings.IniFormat)
    settings.clear()
    window = MainWindow(settings=settings)
    window.resize(1400, 900)
    window.show()
    markdown = '# Notepad View\n\n## 会议记录\n\n保持内容清晰，专注于编辑。\n\n' 
    markdown += '- [x] 中文弹窗\n- [x] 文件页签\n- [ ] 后续事项\n\n'
    markdown += '## 数据示例\n\n~~~json\n{"name": "文档", "count": 3}\n~~~\n\n'
    markdown += '| 项目 | 状态 |\n| --- | --- |\n| 编辑 | 已完成 |\n| 预览 | 已完成 |\n\n'
    for language, source in [('md', markdown), ('json', '{"name": "文档", "count": 3}'),
                              ('sql', 'SELECT name FROM documents;'), ('txt', '记录内容')]:
        window.new_document(language)
        window.editor.setPlainText(source)
    window.document_tabs.setCurrentIndex(0)
    window.path = window.current_session.path = root / '会议记录和后续工作安排.md'
    window.update_title()
    window.show_find()
    window.find_input.setText('内容')
    window.replace_input.setText('文档')
    report = {}
    for theme in ('light', 'dark', 'soft'):
        window.set_theme(theme)
        wait_until(lambda: window._preview_ready and window._preview_target is None)
        QTest.qWait(60)
        window.grab().save(str(output / (theme + '-wide.png')))
        window.theme_menu.popup(window.theme_button.mapToGlobal(QPoint(0, window.theme_button.height())))
        QTest.qWait(60)
        window.theme_menu.grab().save(str(output / (theme + '-theme-menu.png')))
        window.theme_menu.hide()
        dialog = NewDocumentDialog(window)
        dialog.show()
        QTest.qWait(60)
        assert dialog.type_list.visualItemRect(dialog.type_list.item(4)).bottom() < dialog.type_list.viewport().height()
        dialog.grab().save(str(output / (theme + '-new.png')))
        dialog.reject()
        dialog.deleteLater()
        warning = QMessageBox(QMessageBox.Warning, '保存更改', '当前文档有尚未保存的更改。是否保存？',
                              QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, window)
        warning.show()
        QTest.qWait(60)
        warning.grab().save(str(output / (theme + '-warning.png')))
        warning.reject()
        warning.deleteLater()
        file_dialog = QFileDialog(window, '打开文档', str(root), '文档 (*.md *.txt *.json *.xml *.sql);;所有文件 (*)')
        file_dialog.setOption(QFileDialog.DontUseNativeDialog)
        file_dialog.show()
        QTest.qWait(100)
        file_dialog.grab().save(str(output / (theme + '-open.png')))
        file_dialog.reject()
        file_dialog.deleteLater()
        window.resize(900, 620)
        QTest.qWait(100)
        assert window.find_bar.height() <= 112
        assert window.preview.height() >= 250
        window.grab().save(str(output / (theme + '-narrow.png')))
        report[theme] = {'find_height': window.find_bar.height(),
                         'preview_height': window.preview.height(), 'window_width': window.width()}
        window.resize(1400, 900)
    for session in window.sessions:
        session.document.setModified(False)
    window.close()
    app.processEvents()
    (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    capture()
