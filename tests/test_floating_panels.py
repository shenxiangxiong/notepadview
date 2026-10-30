import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFileDialog, QMessageBox

from notepadview.palettes import theme_colors
from test_ui import app, window  # noqa: F401


@pytest.mark.parametrize('theme', ['light', 'dark', 'soft'])
def test_panels_clip_children_and_share_one_pixel_gaps(window, app, theme):
    window.set_theme(theme)
    window.set_mode('split')
    window.resize(1120, 740)
    app.processEvents()
    assert window.editor_split.handleWidth() == window.outer_split.handleWidth() == 1
    assert window.preview_panel.x() - window.source_panel.geometry().right() - 1 == 1
    assert window.outer_split.mapTo(window.centralWidget(), QPoint()).x() == 3
    assert window.outer_split.parentWidget().layout().contentsMargins().top() == 3
    assert window.source_panel.layout().contentsMargins().left() == 1
    assert window.preview_panel.layout().contentsMargins().right() == 1
    assert window.source_panel.mask().contains(QPoint(4, 0))
    assert window.sidebar_layout.spacing() == 0
    assert window.sidebar.mask().isEmpty()
    rail = window.sidebar_rail.geometry()
    sidebar = window.sidebar.geometry()
    assert min(abs(rail.left() - sidebar.right() - 1),
               abs(sidebar.left() - rail.right() - 1)) == 0
    for panel in (window.sidebar_group, window.source_panel, window.preview_panel):
        assert not panel.mask().isEmpty()
        assert not panel.mask().contains(QPoint(0, 0))
        assert panel.mask().contains(panel.rect().center())
    colors = theme_colors(theme)
    assert 0 < QColor(colors['pane']).lightness() - QColor(colors['navigation']).lightness() <= 5
    from pyside6_scintilla import Scintilla
    assert window.editor.styleBack(Scintilla.StylesCommon.LineNumber) == window.editor._color(colors['bg'])
    image = window.centralWidget().grab().toImage()
    for panel in (window.sidebar_group, window.source_panel, window.preview_panel):
        for point in (QPoint(), panel.rect().bottomRight()):
            corner = panel.mapTo(window.centralWidget(), point)
            assert image.pixelColor(corner) == QColor(colors['base'])
    previous = window.source_panel.width()
    window.editor_split.moveSplitter(previous - 40, 1)
    app.processEvents()
    assert window.source_panel.width() < previous
    assert window.preview_panel.x() - window.source_panel.geometry().right() - 1 == 1
    window.set_mode('preview')
    app.processEvents()
    assert not window.preview_panel.mask().contains(QPoint(window.preview_panel.width()-1, 0))


@pytest.mark.parametrize('outcome', ['success', 'cancel', 'failure', 'collision'])
def test_context_save_as_uses_target_without_switching(window, tmp_path, monkeypatch, outcome):
    window.new_document('txt')
    window.editor.insertPlainText('目标\n内容')
    window.editor.set_selection(1, 3)
    target = window.current_session
    target.encoding, target.newline = 'utf-16', '\r\n'
    window.set_mode('preview')
    window.new_document('sql')
    window.editor.setPlainText('select 1;')
    window.editor.set_selection(2, 5)
    current = window.current_session
    window.set_mode('source')
    before = window.workspace.snapshot()
    destination = tmp_path / 'copy.txt'
    if outcome == 'failure':
        destination = tmp_path / 'missing' / 'copy.txt'
    if outcome == 'collision':
        current.path = destination
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: ('' if outcome == 'cancel' else str(destination), ''))
    monkeypatch.setattr(QMessageBox, 'critical', lambda *a: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: QMessageBox.Ok)
    menu = window.file_list.build_context_menu(window.file_list.item_for_session(target))
    next(a for a in menu.actions() if a.text() == '另存为…').trigger()
    assert window.current_session is current
    assert window.editor.source_text() == 'select 1;'
    assert window.editor.selection_state() == before['documents'][1]['selection']
    assert target.selection == before['documents'][0]['selection']
    assert current.view_mode == 'source' and target.view_mode == 'preview'
    assert target.encoding == 'utf-16' and target.newline == '\r\n'
    if outcome == 'success':
        assert destination.read_bytes() == b'\xff\xfe' + '目标\r\n内容'.encode('utf-16-le')
        assert target.path == destination and not target.document.isModified()
    else:
        assert target.path is None and target.document.isModified()


@pytest.mark.parametrize('kind', ['image', 'pdf'])
def test_binary_context_save_as_is_disabled(window, kind):
    window.new_document('txt')
    target = window.current_session
    target.kind = kind
    menu = window.file_list.build_context_menu(window.file_list.item_for_session(target))
    assert not next(a for a in menu.actions() if a.text() == '另存为…').isEnabled()
    assert not window.save_as(session=target)
    target.kind = 'text'


def test_inactive_diagram_save_as_preserves_current_text(window, tmp_path, monkeypatch):
    from notepadview.diagrams import new_diagram, write_diagram
    path = tmp_path / 'original.nvdiagram'
    write_diagram(path, new_diagram('mindmap'))
    assert window.open_document_path(path)
    target = window.current_session
    canvas = target.diagram
    menu = window.file_list.build_context_menu(window.file_list.item_for_session(target))
    assert not next(a for a in menu.actions() if a.text() == '另存为…').isEnabled()
    window.new_document('txt')
    window.editor.insertPlainText('当前文本')
    current = window.current_session
    destination = tmp_path / 'diagram.nvdiagram'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: pytest.fail('只读图表不应另存'))
    assert not window.save_as(session=target)
    assert not destination.exists()
    assert target.diagram is canvas and not target.document.isModified()
    assert window.current_session is current and window.editor.source_text() == '当前文本'
    assert current.document.isModified()
