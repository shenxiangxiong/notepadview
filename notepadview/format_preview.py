"""自动修复的本地差异预览。"""
import difflib

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QSplitter, QVBoxLayout, QWidget,
)
from PySide6.QtCore import Qt


class RepairPreviewDialog(QDialog):
    def __init__(self, before, after, parent=None):
        super().__init__(parent)
        self.setWindowTitle('自动修复预览')
        self.resize(900, 650)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('请核对修改前后内容与差异；确认后仅修改编辑区，不保存原文件。'))
        splitter = QSplitter(Qt.Horizontal, self)
        for name, title, text in (('before', '修改前', before), ('after', '修改后', after)):
            panel = QWidget(splitter)
            column = QVBoxLayout(panel)
            column.addWidget(QLabel(title))
            field = QPlainTextEdit(panel)
            field.setReadOnly(True)
            field.setPlainText(text)
            column.addWidget(field)
            setattr(self, name, field)
        layout.addWidget(splitter)
        layout.addWidget(QLabel('差异（- 删除，+ 新增）'))
        self.diff = QPlainTextEdit(self)
        self.diff.setReadOnly(True)
        self.diff.setPlainText('\n'.join(difflib.unified_diff(
            before.splitlines(), after.splitlines(),
            fromfile='修改前', tofile='修改后', lineterm='')))
        layout.addWidget(self.diff)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.button(QDialogButtonBox.Ok).setText('确认应用')
        buttons.button(QDialogButtonBox.Cancel).setText('取消')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
