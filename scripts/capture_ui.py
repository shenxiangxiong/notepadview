import os
from pathlib import Path
import sys

os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from notepadview.window import MainWindow

root = Path(__file__).resolve().parents[1]
output = root / 'screenshots'
output.mkdir(exist_ok=True)
app = QApplication([])
app.setStyle('Fusion')
app.setFont(QFont('Microsoft YaHei UI', 10))
window = MainWindow(QSettings(str(output / 'capture.ini'), QSettings.IniFormat))
window.resize(1400, 900)
window.show()
QTest.qWait(300)
window.grab().save(str(output / 'startup.png'))
def capture_type_picker():
    dialog = app.activeModalWidget()
    if dialog is not None:
        dialog.grab().save(str(output / 'new-type-picker.png'))
        dialog.reject()
QTimer.singleShot(200, capture_type_picker)
window.choose_new_document()
window.new_document('md')
window.editor.setPlainText('# 项目背景\n\n' + ('本周进展与工作记录。\n\n' * 24) + '## 待办事项\n\n- [ ] 完成初稿')
window.new_document('txt')
window.editor.setPlainText('会议记录\n\n下一步安排\n\n' + ('保留原始文本内容。\n' * 24))
window.document_tabs.setCurrentIndex(0)
for theme in ('light', 'dark'):
    if theme == 'dark':
        window.document_tabs.setCurrentIndex(1)
    window.set_theme(theme)
    QTest.qWait(2500)
    window.grab().save(str(output / (theme + '.png')))
for index, language in [(0, 'md'), (1, 'txt')]:
    window.document_tabs.setCurrentIndex(index)
    for theme in ('light', 'dark'):
        window.set_theme(theme)
        QTest.qWait(500)
        window.grab().save(str(output / (language + '-' + theme + '.png')))
samples = {
    'json': '{"应用":"Notepad View","languages":["Markdown","Text","JSON","XML","SQL"],"enabled":true}',
    'xml': '<?xml version="1.0"?><project name="Notepad View"><!-- 文档编辑 --><item enabled="true">中文内容</item></project>',
    'sql': "select userName, email from Users -- 保留注释\nwhere id=42 and active=true order by userName;",
}
for language, sample in samples.items():
    window.new_document(language)
    window.editor.setPlainText(sample)
    window.format_document()
    for theme in ('light', 'dark'):
        window.set_theme(theme)
        QTest.qWait(600)
        window.grab().save(str(output / (language + '-' + theme + '.png')))
window.resize(900, 620)
window.show_find()
QTest.qWait(500)
window.grab().save(str(output / 'compact.png'))
from PySide6.QtCore import Qt, QPoint
window.resize(1400, 900)
window.new_document('txt')
window.editor.setPlainText('alpha\nbeta\ngamma\ndelta')
QTest.qWait(600)
def point(position):
    editor = window.editor
    return QPoint(editor.pointXFromPosition(position), editor.pointYFromPosition(position) + editor.textHeight(0) // 2)
QTest.mousePress(window.editor.viewport(), Qt.LeftButton, Qt.AltModifier, point(18))
QTest.mouseMove(window.editor.viewport(), point(0))
QTest.mouseRelease(window.editor.viewport(), Qt.LeftButton, Qt.AltModifier, point(0))
window.grab().save(str(output / 'column-selection.png'))
for session in window.sessions:
    session.document.setModified(False)
window.close()
