import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from notepadview.palettes import theme_colors

from test_ui import app, window  # noqa: F401


@pytest.mark.parametrize('theme', ['light', 'dark', 'soft'])
@pytest.mark.parametrize('selected', ['files', 'outline'])
def test_sidebar_icons_have_separate_targets_and_theme_selection(window, theme, selected):
    window.set_theme(theme)
    if selected == 'outline':
        window.toggle_sidebar_page(selected)
    first, second = window.sidebar_buttons.values()
    assert second.y() - (first.y() + first.height()) >= 8
    for button in (first, second):
        button.setAttribute(Qt.WA_UnderMouse, False)
        assert button.toolButtonStyle() == Qt.ToolButtonIconOnly
        assert not button.icon().isNull()
    assert first.toolTip() == '文件' and second.toolTip() == '目录'
    image = window.sidebar_rail.grab().toImage()
    colors = theme_colors(theme)
    for key, button in window.sidebar_buttons.items():
        rect = button.geometry()
        expected = colors['selection' if key == selected else 'navigation']
        assert image.pixelColor(rect.left() + 4, rect.center().y()) == QColor(expected)


def test_files_start_with_tree_and_keep_category_context_menu(window):
    assert not window.sidebar.findChildren(QWidget, 'fileSidebarHeader')
    assert not window.sidebar.findChildren(QWidget, 'fileCountLabel')
    assert not window.sidebar.findChildren(QWidget, 'categoryButton')
    files_layout = window.sidebar_stack.widget(0).layout()
    assert files_layout.count() == 1
    assert files_layout.itemAt(0).widget() is window.file_list
    menu = window.file_list.build_context_menu(None)
    new_action = next(action for action in menu.actions() if action.text() == '新建')
    assert [action.text() for action in new_action.menu().actions()] == ['文件…', '文件分类…']


def test_file_menu_has_rename_for_current_document(window):
    file_action = next(action for action in window.menuBar().actions() if action.text() == '文件')
    file_menu = file_action.menu()
    assert window.rename_action in file_menu.actions()
    assert window.rename_action.text() == '重命名…'
    assert not window.rename_action.isEnabled()
    window.new_document('txt')
    assert window.rename_action.isEnabled()


def test_window_controls_share_the_menu_row(window):
    assert window.windowFlags() & Qt.FramelessWindowHint
    menu = window.menuBar()
    corner = menu.cornerWidget(Qt.TopRightCorner)
    assert corner is not None
    buttons = [window.window_minimize_button, window.window_maximize_button, window.window_close_button]
    assert all(corner.isAncestorOf(button) and button.isVisible() for button in buttons)
    assert all(button.width() == 42 and button.height() == 32 for button in buttons)
    assert [button.toolTip() for button in buttons] == ['最小化', '最大化', '关闭']
    assert menu.geometry().top() == 0


def test_compact_menu_and_controls_do_not_overlap(window):
    window.resize(880, 580)
    QTest.qWait(30)
    menu = window.menuBar()
    controls = menu.cornerWidget(Qt.TopRightCorner)
    assert controls.geometry().left() > max(menu.actionGeometry(action).right() for action in menu.actions())
    assert window.editor.height() > 250


def test_main_window_maximize_and_restore_button(window):
    QTest.mouseClick(window.window_maximize_button, Qt.LeftButton)
    QTest.qWait(50)
    assert window.isMaximized()
    assert window.window_maximize_button.toolTip() == '还原'
    QTest.mouseClick(window.window_maximize_button, Qt.LeftButton)
    QTest.qWait(50)
    assert not window.isMaximized()
    assert window.window_maximize_button.toolTip() == '最大化'
