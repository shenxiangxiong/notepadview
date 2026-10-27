"""检查分类行内编辑、文件右键和双栏往返，并生成实际界面截图。"""
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit

from notepadview.window import MainWindow


def main():
    app = QApplication([])
    app.setStyle('Fusion')
    output = Path(__file__).resolve().parents[1] / 'screenshots/file-workflow'
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory() as directory:
        root = Path(directory)
        saved = root / '资料.md'
        saved.write_text('# 资料文件\n\n右键菜单操作此文件。', encoding='utf-8')
        window = MainWindow(QSettings(str(root / 'review.ini'), QSettings.IniFormat))
        window.set_sidebar_position('left')
        window.open_document_path(saved)
        first = window.current_session
        window.new_document('md')
        window.editor.setPlainText('# 当前文档\n\n右键其他文件时，编辑内容保持不变。')
        window.rename_document_session(window.current_session, '项目记录.md')
        active = window.current_session
        window.show()
        QTest.qWait(200)
        for mode in ('source', 'preview'):
            window.set_mode(mode)
            window.split_button.click()
            assert window.mode == 'split'
            window.split_button.click()
            assert window.mode == mode
        window.set_mode('split')
        category = window.file_list._category_items['']
        window.file_list.setCurrentItem(category)
        window.file_list.editItem(category, 0)
        app.processEvents()
        editor = window.file_list.findChild(QLineEdit)
        assert editor is not None and editor.isVisible()
        editor.setText('我的文档')
        window.grab().save(str(output / 'category-edit.png'))
        QTest.keyClick(editor, Qt.Key_Return)
        QTest.qWait(30)
        assert window.default_category_name == '我的文档'
        target = window.file_list.item_for_session(first)
        position = window.file_list.visualItemRect(target).center()
        def close_native_popup():
            popup = app.activePopupWidget()
            if popup is not None:
                popup.close()
        QTimer.singleShot(100, close_native_popup)
        QTest.mousePress(window.file_list.viewport(), Qt.RightButton, pos=position)
        QTest.mouseRelease(window.file_list.viewport(), Qt.RightButton, pos=position)
        QTest.qWait(120)
        assert window.current_session is active
        menu = window.file_list.build_context_menu(target)
        menu.popup(window.file_list.viewport().mapToGlobal(position))
        QTest.qWait(100)
        assert window.current_session is active
        window.grab().save(str(output / 'file-context.png'))
        menu.grab().save(str(output / 'context-menu.png'))
        menu.close()
        window.new_menu.popup(window.menuBar().mapToGlobal(window.menuBar().rect().bottomLeft()))
        QTest.qWait(80)
        window.new_menu.grab().save(str(output / 'new-menu.png'))
        window.new_menu.close()
        window.close()
    report = {'inline_category_edit': True, 'right_click_keeps_document': True,
              'split_toggle_source_and_preview': True, 'new_file_and_category_menu': True}
    (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
