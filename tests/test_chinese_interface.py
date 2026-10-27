import errno
from types import SimpleNamespace

from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox
from PySide6.QtWebEngineCore import QWebEngineContextMenuRequest

from test_ui import app, window  # noqa: F401


def actions(menu):
    return {action.text(): action for action in menu.actions() if not action.isSeparator()}


def test_source_menu_is_chinese_and_respects_edit_state(window):
    editor = window.editor
    editor.setPlainText('测试文本')
    editor.set_selection(0, 2)
    QApplication.clipboard().setText('粘贴内容')
    menu = editor.build_context_menu()
    entries = actions(menu)
    assert list(entries) == ['撤销', '重做', '剪切', '复制', '粘贴', '删除', '全选'] + [label for label, _ in editor.format_operations()]
    assert entries['复制'].isEnabled()
    assert not entries['撤销'].isEnabled()
    entries['复制'].trigger()
    assert QApplication.clipboard().text() == '测试'
    editor.setReadOnly(True)
    try:
        readonly = actions(editor.build_context_menu())
        assert readonly['复制'].isEnabled()
        assert all(not readonly[name].isEnabled() for name in ('剪切', '粘贴', '删除'))
    finally:
        editor.setReadOnly(False)
        menu.deleteLater()


def test_source_menu_delete_and_undo(window):
    editor = window.editor
    editor.setPlainText('测试文本')
    editor.set_selection(0, 2)
    actions(editor.build_context_menu())['删除'].trigger()
    assert editor.toPlainText() == '文本'
    actions(editor.build_context_menu())['撤销'].trigger()
    assert editor.toPlainText() == '测试文本'


def test_source_menu_delete_preserves_rectangle_carets(window):
    from pyside6_scintilla import Scintilla
    editor = window.editor
    editor.setPlainText('abc\ndef\nghi')
    editor.setSelectionMode(Scintilla.SelectionMode.Rectangle)
    editor.setRectangularSelectionAnchor(0)
    editor.setRectangularSelectionCaret(9)
    menu = editor.build_context_menu()
    actions(menu)['删除'].trigger()
    assert editor.toPlainText() == 'bc\nef\nhi'
    assert editor.selections() == 3
    QTest.keyClicks(editor, 'X')
    assert editor.toPlainText() == 'Xbc\nXef\nXhi'


def test_real_source_right_click_displays_chinese_names(window):
    editor = window.editor
    editor.setPlainText('右键菜单')
    seen = []

    def capture():
        popup = QApplication.activePopupWidget()
        if isinstance(popup, QMenu):
            seen.extend(actions(popup))
            popup.close()

    QTimer.singleShot(50, capture)
    target = editor.viewport()
    point = target.rect().center()
    event = QContextMenuEvent(QContextMenuEvent.Mouse, point, target.mapToGlobal(point))
    QApplication.sendEvent(target, event)
    assert seen == ['撤销', '重做', '剪切', '复制', '粘贴', '删除', '全选'] + [label for label, _ in editor.format_operations()]


def request(selected='', link='', image=''):
    return SimpleNamespace(
        selectedText=lambda: selected, linkUrl=lambda: QUrl(link),
        mediaUrl=lambda: QUrl(image),
        mediaType=lambda: (QWebEngineContextMenuRequest.MediaType.MediaTypeImage if image else
                           QWebEngineContextMenuRequest.MediaType.MediaTypeNone))


def test_preview_menu_has_names_and_copies_selection(window):
    menu = window.preview.build_context_menu(request(selected='预览文本'))
    entries = actions(menu)
    assert list(entries) == ['复制', '全选']
    entries['复制'].trigger()
    assert QApplication.clipboard().text() == '预览文本'
    blank = actions(window.preview.build_context_menu(request()))
    assert list(blank) == ['复制', '全选']
    assert not blank['复制'].isEnabled()


def test_preview_link_and_image_actions_are_chinese(window):
    link = 'https://example.com/note'
    image = 'https://example.com/image.png'
    entries = actions(window.preview.build_context_menu(request(link=link, image=image)))
    assert {'复制链接地址', '在浏览器中打开链接', '复制图片', '复制图片地址'} <= entries.keys()
    assert all(name and any('\u4e00' <= char <= '\u9fff' for char in name) for name in entries)
    entries['复制链接地址'].trigger()
    assert QApplication.clipboard().text() == link
    entries['复制图片地址'].trigger()
    assert QApplication.clipboard().text() == image


def test_open_and_save_errors_are_chinese(window, tmp_path, monkeypatch):
    import notepadview.window as module
    messages = []
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: messages.append(args[2]))
    assert not window.open_document_path(tmp_path / 'missing.txt')
    assert 'No such file' not in messages[-1]
    assert '找不到' in messages[-1] or '不存在' in messages[-1]

    def fail(*args):
        raise PermissionError(errno.EACCES, 'Permission denied')

    monkeypatch.setattr(module, 'save_document', fail)
    assert not window.save_to(tmp_path / 'note.txt')
    assert 'Permission denied' not in messages[-1]
    assert '权限' in messages[-1]
    assert not window.export_to(tmp_path / 'note.html')
    assert 'Permission denied' not in messages[-1]


def test_workspace_errors_are_chinese(window, monkeypatch):
    messages = []
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: messages.append(args[2]))

    def fail(*args):
        raise OSError(errno.ENOSPC, 'No space left on device')

    monkeypatch.setattr(window.workspace.store, 'save', fail)
    assert not window.workspace.flush()
    assert 'No space left' not in messages[-1]
    assert '空间' in messages[-1]
    monkeypatch.undo()


def test_find_input_standard_menu_is_chinese(window):
    window.find_input.setText('测试')
    window.find_input.selectAll()
    menu = window.find_input.createStandardContextMenu()
    try:
        assert all(any('\u4e00' <= char <= '\u9fff' for char in name) for name in actions(menu))
    finally:
        menu.deleteLater()


def test_real_preview_right_click_displays_chinese_names(window):
    window.new_document('md')
    window.editor.setPlainText('# 预览右键测试\n\n可以复制的内容。')
    window.render_preview()
    for _ in range(100):
        QTest.qWait(50)
        if window._preview_ready:
            break
    assert window._preview_ready
    seen = []
    timer = QTimer(window)
    timer.setInterval(20)

    def capture():
        menu = QApplication.activePopupWidget()
        if isinstance(menu, QMenu):
            seen.extend(actions(menu))
            menu.close()

    timer.timeout.connect(capture)
    timer.start()
    try:
        target = window.preview.focusProxy() or window.preview
        QTest.mouseClick(target, Qt.RightButton, pos=target.rect().center())
        QTest.qWait(300)
    finally:
        timer.stop()
    assert seen == ['复制', '全选']
