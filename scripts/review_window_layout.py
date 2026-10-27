"""检查单层顶部栏、左右侧栏、品牌图标和已有文档操作。"""
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolBar

from notepadview.window import MainWindow


def main():
    app = QApplication([])
    app.setStyle('Fusion')
    app.setFont(QFont('Microsoft YaHei UI', 10))
    output = Path(__file__).resolve().parents[1] / 'screenshots/sidebar-brand'
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory() as directory:
        settings = QSettings(str(Path(directory) / 'review.ini'), QSettings.IniFormat)
        window = MainWindow(settings)
        window.show()
        notes = window.create_category('工作笔记')
        window.new_document('md', category_id=notes)
        window.editor.insertPlainText('# 项目记录\n\n## 本周进展\n\n- 整理文档\n- 记录设计\n\n## 待办事项\n\n核对发布内容。')
        assert window.rename_document_session(window.current_session, '项目记录.md')
        session = window.current_session
        code = window.create_category('代码片段')
        window.new_document('json', category_id=code)
        window.editor.insertPlainText('{"主题": "护眼", "自动恢复": true}')
        assert window.rename_document_session(window.current_session, '编辑器设置.json')
        window.document_tabs.setCurrentIndex(window._session_tab_index(session))
        for theme in ('light', 'dark', 'soft'):
            window.theme_actions[theme].trigger()
            for position in ('left', 'right'):
                window.sidebar_position_actions[position].trigger()
                for width, height, size in ((1400, 900, 'wide'), (880, 580, 'compact')):
                    window.resize(width, height)
                    QTest.qWait(250)
                    menu = window.menuBar()
                    controls = menu.cornerWidget(Qt.TopRightCorner)
                    assert controls.geometry().left() > max(menu.actionGeometry(a).right() for a in menu.actions())
                    brand_right = window.brand.mapTo(menu, QPoint(window.brand.width(), 0)).x()
                    assert brand_right < min(menu.actionGeometry(a).left() for a in menu.actions())
                    assert window.brand_icon.isVisible() and not window.brand_icon.pixmap().isNull()
                    assert window.centralWidget().y() == menu.height()
                    assert not window.findChildren(QToolBar, 'viewToolbar')
                    rail_x = window.sidebar_rail.mapTo(window, QPoint()).x()
                    side_x = window.sidebar.mapTo(window, QPoint()).x()
                    editor_x = window.editor_split.mapTo(window, QPoint()).x()
                    assert (rail_x < side_x < editor_x) if position == 'left' else (editor_x < side_x < rail_x)
                    for button in (*window.header_buttons.values(), window.window_close_button):
                        button.setAttribute(Qt.WA_UnderMouse, False)
                    window.grab().save(str(output / f'{theme}-{position}-{size}.png'))
            close = window.window_close_button
            close.setAttribute(Qt.WA_UnderMouse, True)
            image = close.grab().toImage()
            assert image.pixelColor(4, close.height() // 2) == QColor('#c42b1c')
            cross = image.pixelColor(close.width() // 2, close.height() // 2)
            assert min(cross.red(), cross.green(), cross.blue()) >= 235
            close.setAttribute(Qt.WA_UnderMouse, False)
            for name, button, popup in (('settings', window.settings_button, window.settings_menu),
                                        ('theme', window.theme_button, window.theme_menu),
                                        ('position', window.settings_button, window.sidebar_position_menu)):
                popup.popup(button.mapToGlobal(QPoint(0, button.height())))
                QTest.qWait(80)
                popup.grab().save(str(output / f'{theme}-{name}-menu.png'))
                popup.close()
            QTest.mouseClick(window.sidebar_buttons['outline'], Qt.LeftButton)
            QTest.qWait(100)
            assert window.sidebar_title.text() == '目录'
            window.grab().save(str(output / f'{theme}-outline.png'))
            QTest.mouseClick(window.sidebar_buttons['outline'], Qt.LeftButton)
            QTest.qWait(100)
            assert window.sidebar.isHidden() and window.sidebar_rail.isVisible()
            window.grab().save(str(output / f'{theme}-collapsed.png'))
            QTest.mouseClick(window.sidebar_buttons['files'], Qt.LeftButton)
            assert window.sidebar.isVisible()
        QTest.mouseClick(window.window_maximize_button, Qt.LeftButton)
        QTest.qWait(150)
        assert window.isMaximized()
        assert window.screen().availableGeometry().contains(window.frameGeometry())
        window.grab().save(str(output / 'maximized.png'))
        QTest.mouseClick(window.window_maximize_button, Qt.LeftButton)
        QTest.qWait(100)
        assert not window.isMaximized()
        window.view_actions['source'].trigger()
        window.sidebar_position_actions['left'].trigger()
        assert window.flush_workspace()
        window.close()
        reopened = MainWindow(settings)
        assert reopened.current_session.name == '项目记录.md'
        assert len(reopened.sessions) == 2
        assert reopened.mode == 'source' and reopened.theme == 'soft'
        assert reopened.sidebar_position == 'left'
        reopened.close()
    report = {'themes': ['light', 'dark', 'soft'], 'compact_width': 880,
              'single_header': True, 'right_icon_navigation': True,
              'sidebar_collapse': True, 'close_hover_white_cross': True,
              'maximized_within_work_area': True, 'draft_name_restored': True,
              'selected_view_restored': True, 'theme_restored': True}
    report.update(sidebar_positions=['left', 'right'], sidebar_position_restored=True, brand_logo=True)
    (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
