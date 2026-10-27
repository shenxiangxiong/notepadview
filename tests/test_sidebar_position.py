import pytest
from PySide6.QtCore import QPoint, Qt

from test_ui import app, window  # noqa: F401


def x(widget, window):
    return widget.mapTo(window, QPoint()).x()


@pytest.mark.parametrize('position', ['left', 'right'])
def test_sidebar_and_navigation_move_together_without_losing_document(window, app, position):
    window.new_document('md')
    window.editor.setPlainText('# 保留文档\n\n正文')
    document = window.current_session
    window.toggle_sidebar_page('outline')
    window.sidebar_position_actions[position].trigger()
    app.processEvents()
    assert window.sidebar.parentWidget() is window.sidebar_group
    assert window.sidebar_rail.parentWidget() is window.sidebar_group
    assert window.sidebar_stack.currentWidget() is window.outline
    assert window.current_session is document
    assert window.editor.source_text() == '# 保留文档\n\n正文'
    assert window.settings.value('sidebar_position') == position
    assert sum(action.isChecked() for action in window.sidebar_position_actions.values()) == 1
    if position == 'left':
        assert x(window.sidebar_rail, window) < x(window.sidebar, window) < x(window.editor_split, window)
    else:
        assert x(window.editor_split, window) < x(window.sidebar, window) < x(window.sidebar_rail, window)


@pytest.mark.parametrize('position', ['left', 'right'])
def test_collapsed_sidebar_keeps_navigation_and_reopens_at_same_width(window, app, position):
    window.set_sidebar_position(position)
    sidebar_index = window.outer_split.indexOf(window.sidebar_group)
    sizes = [800, 360] if sidebar_index == 1 else [360, 800]
    window.outer_split.setSizes(sizes)
    app.processEvents()
    previous_width = window.sidebar_group.width()
    window.set_sidebar_visible(False)
    app.processEvents()
    assert window.sidebar.isHidden() and window.sidebar_rail.isVisible()
    assert window.sidebar_group.width() == window.sidebar_rail.width() + 2
    window.set_sidebar_position('right' if position == 'left' else 'left')
    assert window.sidebar.isHidden()
    window.toggle_sidebar_page('files')
    app.processEvents()
    assert window.sidebar.isVisible()
    assert abs(window.sidebar_group.width() - previous_width) <= 2


def test_sidebar_position_restores_on_restart_with_safe_fallback(window):
    window.set_sidebar_position('left')
    reopened = type(window)(settings=window.settings)
    try:
        assert reopened.sidebar_position == 'left'
        assert reopened.outer_split.widget(0) is reopened.sidebar_group
        assert reopened.sidebar_position_actions['left'].isChecked()
    finally:
        reopened.close()
    window.settings.setValue('sidebar_position', 'invalid')
    reopened = type(window)(settings=window.settings)
    try:
        assert reopened.sidebar_position == 'right'
        assert reopened.outer_split.widget(1) is reopened.sidebar_group
    finally:
        reopened.close()


def test_switching_sidebar_page_keeps_resized_width(window, app):
    window.outer_split.setSizes([800, 390])
    app.processEvents()
    width = window.sidebar_group.width()
    window.toggle_sidebar_page('outline')
    app.processEvents()
    assert abs(window.sidebar_group.width() - width) <= 2


def test_brand_logo_precedes_product_name(window):
    assert not window.brand_icon.pixmap().isNull()
    assert window.brand_icon.width() >= 20
    assert window.brand_icon.parentWidget() is window.brand.parentWidget()
    assert x(window.brand_icon, window) < x(window.brand, window)
    assert window.menuBar().cornerWidget(Qt.TopLeftCorner) is window.brand_group
    assert window.brand_group.isVisible()


def test_initial_sidebar_keeps_space_for_file_names(window):
    assert window.sidebar_group.width() >= 310
