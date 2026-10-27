"""Capture the real desktop tree and find dialog in each supported theme."""
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import perf_counter, sleep

os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QSettings
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from notepadview.window import MainWindow


def main():
    app = QApplication([])
    app.setStyle('Fusion')
    app.setFont(QFont('Microsoft YaHei UI', 10))
    output = Path(__file__).resolve().parents[1] / 'screenshots' / 'workspace-tree'
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory() as directory:
        window = MainWindow(QSettings(str(Path(directory) / 'review.ini'), QSettings.IniFormat))
        window.show()
        window.new_document('md')
        window.editor.insertPlainText('# Notepad View\n\n## 开发记录\n\n文档保留在工作区中。\n\n- 文件分类\n- 自动恢复\n')
        notes = window.current_session
        category = window.create_category('工作笔记')
        window.move_sessions_to_category([notes], category)
        window.new_document('sql')
        window.editor.insertPlainText('SELECT id, name\nFROM documents\nWHERE archived = 0;')
        code = window.current_session
        snippets = window.create_category('代码片段')
        window.move_sessions_to_category([code], snippets)
        window.new_document('json')
        window.editor.insertPlainText('{\n    "theme": "light",\n    "autosave": true\n}')
        window.new_document('txt')
        window.document_tabs.setCurrentIndex(window._session_tab_index(notes))
        for theme in ('light', 'dark', 'soft'):
            window.set_theme(theme)
            for width, height, label in ((1400, 900, 'wide'), (900, 620, 'compact')):
                window.resize(width, height)
                QTest.qWait(350)
                window.grab().save(str(output / f'{theme}-{label}.png'))
            window.show_find()
            QTest.qWait(100)
            window.find_bar.grab().save(str(output / f'{theme}-find.png'))
            window.hide_find()
            menu = window.file_list.build_context_menu(window.file_list.item_for_session(notes))
            menu.popup(window.file_list.mapToGlobal(window.file_list.rect().center()))
            QTest.qWait(100)
            menu.grab().save(str(output / f'{theme}-menu.png'))
            menu.close()
            menu.deleteLater()
        window.new_document('json')
        app.clipboard().setText('This is not JSON. 中文内容\n' * 24000)
        started = perf_counter()
        window.editor.paste()
        paste_ms = (perf_counter() - started) * 1000
        started = perf_counter()
        assert window.flush_workspace()
        flush_ms = (perf_counter() - started) * 1000
        active_id = window.current_session.id
        window.close()
        reopened = MainWindow(window.settings)
        assert reopened.current_session.id == active_id
        assert reopened.editor.document().isModified()
        assert len(reopened.sessions) == 5
        reopened.close()
        report = {'invalid_json_paste_ms': round(paste_ms, 2), 'workspace_flush_ms': round(flush_ms, 2),
                  'restored_documents': 5, 'restored_dirty': True, 'themes': ['light', 'dark', 'soft']}
        (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report))


if __name__ == '__main__':
    main()
