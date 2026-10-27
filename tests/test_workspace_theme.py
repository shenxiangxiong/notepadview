import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction, QColor, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QListView, QMainWindow, QMenu,
    QStyle, QStyleFactory, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from notepadview.palettes import THEMES, theme_colors
from notepadview.theme import PopupThemeFilter, apply_theme, theme_stylesheet


def test_nv_palette_matches_portal_and_has_spin_assets():
    from pathlib import Path

    colors = theme_colors('nv')
    assert colors['bg'].lower() == '#122035'
    assert colors['pane'].lower() == '#14243d'
    assert colors['fg'].lower() == '#cdd2d7'
    assert colors['header'].lower() == '#15253e'
    assert colors['preview_frame'].lower() == '#1c3253'
    assert colors['paper'].lower() == '#e7edf4'
    from notepadview.rendering import render_html, render_plain_text, render_code_preview
    from notepadview.syntax import style_for_theme
    assert style_for_theme('nv') is style_for_theme('dark')
    for html in (render_html('# 标题', 'nv'), render_plain_text('内容', 'nv'),
                 render_code_preview('print(1)', 'python', 'nv')):
        assert '#e7edf4' not in html
        assert '#1c3253' in html
        assert '#cdd2d7' in html
    assets = Path(__file__).resolve().parents[1] / 'notepadview' / 'assets'
    for direction in ('up', 'down'):
        assert (assets / f'spin-{direction}-nv.svg').is_file()
    assert colors['accent'] in theme_stylesheet('nv')


@pytest.fixture(scope='session')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(params=QStyleFactory.keys())
def qt_style(app, request):
    previous = app.style().objectName()
    app.setStyle(request.param)
    yield
    app.setStyle(previous)


@pytest.fixture(params=tuple(THEMES))
def themed_window(app, qt_style, request):
    window = QMainWindow()
    window.theme = request.param
    window.setStyleSheet(theme_stylesheet(window.theme))
    window.resize(420, 320)
    popup_filter = PopupThemeFilter(app)
    app.installEventFilter(popup_filter)
    window.show()
    app.processEvents()
    yield window
    for popup in app.topLevelWidgets():
        if isinstance(popup, QMenu) or popup.metaObject().className() == 'QComboBoxPrivateContainer':
            popup.hide()
    window.close()
    window.deleteLater()
    app.processEvents()
    app.removeEventFilter(popup_filter)


def assert_rounded_popup(popup):
    rect = popup.rect()
    mask = popup.mask()
    assert not mask.isEmpty()
    assert mask.boundingRect() == rect
    assert mask.contains(rect.center())
    assert mask.contains(QPoint(rect.center().x(), rect.top()))
    assert mask.contains(QPoint(rect.right(), rect.center().y()))
    for corner in (rect.topLeft(), rect.topRight(), rect.bottomLeft(), rect.bottomRight()):
        assert not mask.contains(corner)


@pytest.mark.parametrize('custom_view', [False, True])
def test_combo_popup_has_rounded_window_and_keeps_keyboard_selection(app, themed_window, custom_view):
    combo = QComboBox(themed_window)
    if custom_view:
        combo.setView(QListView())
    combo.addItems(['Light', 'Dark', 'Soft'])
    combo.resize(180, 36)
    combo.show()
    combo.showPopup()
    app.processEvents()
    popup = combo.view().window()

    assert popup.isWindow()
    assert_rounded_popup(popup)
    assert combo.view().mask().isEmpty()
    image = popup.grab().toImage()
    assert image.pixelColor(image.width() // 2, 2) == QColor(theme_colors(themed_window.theme)['bg'])
    QTest.keyClick(combo.view(), Qt.Key_Down)
    QTest.keyClick(combo.view(), Qt.Key_Return)
    app.processEvents()
    assert combo.currentIndex() == 1
    assert not popup.isVisible()


@pytest.fixture
def no_combo_animation(app):
    enabled = app.isEffectEnabled(Qt.UI_AnimateCombo)
    app.setEffectEnabled(Qt.UI_AnimateCombo, False)
    yield
    app.setEffectEnabled(Qt.UI_AnimateCombo, enabled)


@pytest.mark.parametrize('editable', [False, True])
def test_combo_popup_opens_below_control_without_overlap(app, themed_window, no_combo_animation, editable):
    combo = QComboBox(themed_window)
    combo.setEditable(editable)
    combo.addItems(['One', 'Two', 'Three'])
    combo.setCurrentIndex(1)
    combo.setGeometry(30, 40, 180, 36)
    combo.show()
    QTest.qWait(50)
    combo.showPopup()
    app.processEvents()
    popup = combo.view().window()
    control_bottom = combo.mapToGlobal(QPoint(0, combo.height())).y()
    assert popup.geometry().top() >= control_bottom
    combo.hidePopup()


def test_combo_popup_opens_above_near_screen_bottom(app, themed_window, no_combo_animation):
    combo = QComboBox(themed_window)
    combo.addItems(['One', 'Two', 'Three'])
    combo.setGeometry(30, 260, 180, 36)
    screen = themed_window.screen().availableGeometry()
    themed_window.move(screen.left() + 100, screen.bottom() - themed_window.height() - 20)
    combo.show()
    app.processEvents()
    combo.showPopup()
    app.processEvents()
    popup = combo.view().window()
    assert popup.isVisible()
    assert popup.geometry().bottom() < combo.mapToGlobal(QPoint(0, 0)).y()
    assert screen.contains(popup.geometry())
    combo.hidePopup()


def test_combo_popup_in_child_dialog_updates_mask_on_resize_and_reopen(app, themed_window, no_combo_animation):
    dialog = QDialog(themed_window)
    combo = QComboBox(dialog)
    combo.addItems(['One', 'Two', 'Three'])
    layout = QVBoxLayout(dialog)
    layout.addWidget(combo)
    dialog.show()
    combo.showPopup()
    app.processEvents()
    popup = combo.view().window()
    assert QTest.qWaitForWindowExposed(popup)
    original_size = popup.size()
    popup.resize(original_size.width() + 75, original_size.height() + 20)
    app.processEvents()
    assert_rounded_popup(popup)
    combo.hidePopup()

    themed_window.theme = 'dark' if themed_window.theme != 'dark' else 'soft'
    themed_window.setStyleSheet(theme_stylesheet(themed_window.theme))
    combo.showPopup()
    app.processEvents()
    assert QTest.qWaitForWindowExposed(popup)
    assert_rounded_popup(popup)
    assert popup.palette().color(QPalette.Window) == QColor(theme_colors(themed_window.theme)['bg'])
    combo.hidePopup()
    dialog.close()


def test_menu_and_submenu_are_rounded_and_actions_still_trigger(app, themed_window):
    menu = themed_window.menuBar().addMenu('File')
    submenu = menu.addMenu('New')
    submenu.addAction('Disabled').setEnabled(False)
    action = submenu.addAction('Document')
    triggered = []
    action.triggered.connect(lambda: triggered.append(True))
    menu.popup(themed_window.mapToGlobal(QPoint(15, 40)))
    app.processEvents()
    assert_rounded_popup(menu)
    menu.setActiveAction(submenu.menuAction())
    QTest.keyClick(menu, Qt.Key_Right)
    app.processEvents()
    assert submenu.isVisible()
    assert_rounded_popup(submenu)
    assert submenu.palette().color(QPalette.Window) == QColor(theme_colors(themed_window.theme)['bg'])
    submenu.setActiveAction(action)
    QTest.keyClick(submenu, Qt.Key_Return)
    app.processEvents()
    assert triggered == [True]
    assert not submenu.isVisible()
    assert not menu.isVisible()


def test_menu_mask_tracks_dynamic_content_resize_and_theme_reopen(app, themed_window):
    menu = QMenu(themed_window)
    menu.addAction('Open')
    menu.aboutToShow.connect(lambda: menu.addAction('A much longer dynamically added document action'))
    menu.popup(themed_window.mapToGlobal(QPoint(20, 50)))
    app.processEvents()
    assert_rounded_popup(menu)
    menu.resize(menu.width() + 41, menu.height() + 23)
    app.processEvents()
    assert_rounded_popup(menu)
    menu.hide()
    themed_window.theme = 'dark' if themed_window.theme != 'dark' else 'soft'
    themed_window.setStyleSheet(theme_stylesheet(themed_window.theme))
    menu.popup(themed_window.mapToGlobal(QPoint(20, 50)))
    app.processEvents()
    assert_rounded_popup(menu)
    image = menu.grab().toImage()
    assert image.pixelColor(image.width() // 2, 2) == QColor(theme_colors(themed_window.theme)['bg'])
    QTest.keyClick(menu, Qt.Key_Escape)
    assert not menu.isVisible()


def test_menu_mouse_click_ignores_disabled_item_and_triggers_enabled_item(app, themed_window):
    menu = QMenu(themed_window)
    disabled = menu.addAction('Unavailable')
    disabled.setEnabled(False)
    enabled = menu.addAction('Open document')
    triggered = []
    disabled.triggered.connect(lambda: triggered.append('disabled'))
    enabled.triggered.connect(lambda: triggered.append('enabled'))
    menu.popup(themed_window.mapToGlobal(QPoint(20, 50)))
    assert QTest.qWaitForWindowExposed(menu)
    QTest.mouseClick(menu, Qt.LeftButton, pos=menu.actionGeometry(disabled).center())
    assert triggered == []
    assert menu.isVisible()
    QTest.mouseClick(menu, Qt.LeftButton, pos=menu.actionGeometry(enabled).center())
    assert triggered == ['enabled']
    assert not menu.isVisible()


def test_filter_leaves_regular_windows_and_unowned_menus_unmasked(app, themed_window):
    dialog = QDialog(themed_window)
    dialog.show()
    menu = QMenu()
    menu.addAction('Unowned')
    menu.popup(themed_window.mapToGlobal(QPoint(10, 10)))
    app.processEvents()
    assert themed_window.mask().isEmpty()
    assert dialog.mask().isEmpty()
    assert menu.mask().isEmpty()
    assert menu.styleSheet() == ''
    menu.close()
    dialog.close()


def test_file_tree_uses_workspace_palette(app, themed_window):
    sidebar = QWidget(themed_window)
    sidebar.setObjectName('sidebar')
    layout = QVBoxLayout(sidebar)
    tree = QTreeWidget(sidebar)
    tree.setObjectName('fileTree')
    tree.setHeaderLabels(['Name', 'Count / format'])
    group = QTreeWidgetItem(tree, ['Documents', '12'])
    document = QTreeWidgetItem(group, ['notes.md', 'MD'])
    group.setExpanded(True)
    tree.setColumnWidth(0, 190)
    layout.addWidget(tree)
    themed_window.setCentralWidget(sidebar)
    app.processEvents()
    colors = theme_colors(themed_window.theme)

    assert tree.header().palette().color(QPalette.WindowText) == QColor(colors['muted'])
    assert tree.palette().color(QPalette.Highlight) == QColor(colors['selection'])
    assert tree.palette().color(QPalette.HighlightedText) == QColor(colors['fg'])
    tree.setCurrentItem(document)
    assert document.isSelected()
    assert tree.visualItemRect(document).height() >= 30
    assert document.text(1) == 'MD'
    assert group.text(1) == '12'


@pytest.mark.parametrize('qt_style', ['Fusion', 'Windows'], indirect=True)
def test_apply_theme_without_internal_find_close_button(app, qt_style):
    class Editor(QWidget):
        def set_theme(self, theme):
            self.theme = theme

    previous_filter = getattr(app, '_notepad_popup_filter', None)
    window = QMainWindow()
    window.editor = Editor(window)
    window.settings_menu = QMenu(window)
    window.theme_menu = QMenu(window)
    window.theme_actions = {}
    for theme in THEMES:
        action = QAction(theme, window)
        action.setCheckable(True)
        window.theme_menu.addAction(action)
        window.theme_actions[theme] = action
    window.header_buttons = {}
    window.sidebar_buttons = {}
    window.undo_action = QAction(window)
    window.redo_action = QAction(window)
    window.export_action = QAction(window)
    try:
        for theme in THEMES:
            window.theme = theme
            apply_theme(window)
            installed_filter = app._notepad_popup_filter
            apply_theme(window)
            assert app._notepad_popup_filter is installed_filter
            assert window.editor.theme == theme
            assert all(action.isChecked() == (key == theme)
                       for key, action in window.theme_actions.items())
            colors = theme_colors(theme)
            for menu in (window.settings_menu, window.theme_menu):
                assert menu.palette().color(QPalette.Window) == QColor(colors['bg'])
                assert menu.palette().color(QPalette.Base) == QColor(colors['bg'])
                assert menu.palette().color(QPalette.Highlight) == QColor(colors['hover'])
                assert_rounded_popup(menu)
            for control in (window.undo_action, window.redo_action, window.export_action):
                assert not control.icon().isNull()
    finally:
        window.close()
        window.deleteLater()
        if previous_filter is None and hasattr(app, '_notepad_popup_filter'):
            app.removeEventFilter(app._notepad_popup_filter)
            del app._notepad_popup_filter
        app.processEvents()
