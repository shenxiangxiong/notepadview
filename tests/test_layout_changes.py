import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QToolBar, QToolButton

from test_ui import app, window  # noqa: F401


def test_header_is_a_single_row_with_compact_controls(window):
    actions = window.menuBar().actions()
    assert window.menuBar().cornerWidget(Qt.TopLeftCorner) is window.brand_group
    assert [action.text() for action in actions] == ['文件', '编辑', '帮助']
    assert not window.findChildren(QToolBar, 'viewToolbar')
    assert not hasattr(window, 'theme_combo')
    controls = [window.theme_button, window.split_button, window.settings_button,
                window.window_minimize_button, window.window_maximize_button, window.window_close_button]
    positions = [button.mapTo(window, QPoint()).x() for button in controls]
    assert positions == sorted(positions)
    assert all(button.toolButtonStyle() == Qt.ToolButtonIconOnly for button in controls)
    assert all(not button.icon().isNull() for button in controls)
    assert window.settings_button.menu() is window.settings_menu
    assert set(window.theme_actions) == {'light', 'dark', 'soft'}


def test_sidebar_uses_icons_on_the_right_and_can_collapse(window):
    assert window.outer_split.widget(0) is window.document_area
    assert window.outer_split.widget(1) is window.sidebar_group
    assert window.sidebar_rail.width() <= 48
    assert window.sidebar_rail.mapTo(window, QPoint()).x() >= window.sidebar.mapTo(window, QPoint()).x() + window.sidebar.width()
    assert list(window.sidebar_buttons) == ['files', 'outline']
    outline = window.sidebar_buttons['outline']
    QTest.mouseClick(outline, Qt.LeftButton)
    assert window.sidebar_stack.currentWidget() is window.outline
    assert window.sidebar_title.text() == '目录'
    assert outline.isChecked() and window.sidebar_action.isChecked()
    QTest.mouseClick(outline, Qt.LeftButton)
    assert window.sidebar.isHidden()
    assert not outline.isChecked() and not window.sidebar_action.isChecked()
    assert window.sidebar_rail.isVisible()
    QTest.mouseClick(window.sidebar_buttons['files'], Qt.LeftButton)
    assert window.sidebar.isVisible() and window.sidebar_title.text() == '文件'


def test_settings_selection_changes_view_and_next_startup_default(window):
    texts = [action.text() for action in window.settings_menu.actions()]
    assert '默认启动视图' not in texts
    assert all(label in texts for label in ('仅源码', '双栏', '仅预览', '侧边栏'))
    window.view_actions['preview'].trigger()
    assert window.mode == window.default_view == 'preview'
    assert window.settings.value('default_view') == 'preview'
    assert not window.split_button.isChecked()
    QTest.mouseClick(window.split_button, Qt.LeftButton)
    assert window.mode == window.default_view == 'split'
    assert window.split_button.isChecked()
    assert sum(action.isChecked() for action in window.view_actions.values()) == 1


def test_view_shortcuts_work_from_the_button_menu(window):
    window.activateWindow()
    window.raise_()
    assert QTest.qWaitForWindowActive(window)
    window.editor.setFocus()
    QTest.keyClick(window.editor, Qt.Key_1, Qt.ControlModifier)
    assert window.mode == 'source'
    QTest.keyClick(window.editor, Qt.Key_3, Qt.ControlModifier)
    assert window.mode == 'preview'


@pytest.mark.parametrize('operation', ['dialog', 'next', 'format'])
def test_temporary_source_reveal_keeps_selected_startup_view(window, operation):
    window.new_document('md')
    window.editor.setPlainText('target')
    window.view_actions['preview'].trigger()
    window.find_input.setText('target')
    if operation == 'dialog':
        window.show_find()
        window.hide_find()
    elif operation == 'next':
        assert window.find_next()
    else:
        window.wrap_selection('**', '**', '文字')
    assert window.source_panel.isVisible() == (operation == 'format')
    assert window.default_view == window.settings.value('default_view') == 'preview'
    assert window.view_actions['preview'].isChecked()
    assert window.flush_workspace()
    reopened = type(window)(settings=window.settings)
    try:
        assert reopened.mode == 'preview'
    finally:
        reopened.close()


def test_new_documents_render_in_tree_without_close_buttons(window):
    window.new_document('md')
    window.new_document('json')

    category = window.file_list.topLevelItem(0)
    assert category.childCount() == 2
    assert window.file_list.currentItem().data(0, Qt.UserRole) is window.current_session
    for index in range(category.childCount()):
        assert category.child(index).text(0).startswith(f'{index + 1}. ')
    assert not window.file_list.findChildren(QToolButton, 'fileCloseButton')


def test_file_row_click_activates_matching_document(window):
    window.new_document('md')
    window.editor.setPlainText('markdown')
    window.new_document('txt')
    window.editor.setPlainText('plain text')

    item = window.file_list.topLevelItem(0).child(0)
    item.setSelected(True)
    window.file_list.itemClicked.emit(item, 0)

    assert window.editor.toPlainText() == 'markdown'
    assert window.current_session.document_format == 'md'


def test_copy_action_routes_to_preview_when_preview_has_focus(window, monkeypatch):
    calls = []
    monkeypatch.setattr(window.preview.page(), 'triggerAction', lambda action: calls.append(action))
    window.preview.setFocus(Qt.OtherFocusReason)

    window.copy_current()

    assert calls

