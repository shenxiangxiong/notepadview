from pathlib import Path
import sys

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTextBrowser, QVBoxLayout


def find_readme(*, executable=None, frozen=None):
    executable = Path(executable or sys.executable)
    frozen = getattr(sys, 'frozen', False) if frozen is None else frozen
    if frozen:
        candidates = (executable.parent / 'README.md',
                      executable.parent.parent / 'Resources' / 'README.md',
                      Path(getattr(sys, '_MEIPASS', executable.parent)) / 'README.md')
    else:
        candidates = (Path(__file__).resolve().parent.parent / 'README.md',)
    return next((path for path in candidates if path.is_file()), candidates[0])


class HelpDialog(QDialog):
    def __init__(self, parent=None, *, readme=None):
        super().__init__(parent)
        self.setWindowTitle('Notepad View 使用帮助')
        self.resize(960, 720)
        self.browser = QTextBrowser(self)
        self.browser.setOpenExternalLinks(True)
        self.browser.document().setDefaultStyleSheet(
            'body { line-height: 1.5; } '
            'th, td { padding: 6px; } '
            'pre { white-space: pre-wrap; }')
        readme = Path(readme) if readme is not None else find_readme()
        self.browser.document().setBaseUrl(QUrl.fromLocalFile(str(readme.resolve())))
        try:
            self.browser.setMarkdown(readme.read_text(encoding='utf-8-sig'))
        except (OSError, UnicodeError):
            self.browser.setPlainText('帮助文档暂时无法读取，请检查程序目录中的 README.md 是否完整。')
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.close)
        layout = QVBoxLayout(self)
        layout.addWidget(self.browser)
        layout.addWidget(buttons)
