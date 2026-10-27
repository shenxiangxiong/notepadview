from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout, QLineEdit, QMessageBox

import pytest

from tests.test_ui import app, window, wait_until


def drop_urls(target, urls):
    mime = QMimeData()
    mime.setUrls(urls)
    actions = Qt.CopyAction | Qt.MoveAction
    enter = QDragEnterEvent(QPoint(10, 10), actions, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(target, enter)
    if not enter.isAccepted():
        return False, False, Qt.IgnoreAction
    move = QDragMoveEvent(QPoint(10, 10), actions, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(target, move)
    assert move.isAccepted()
    drop = QDropEvent(QPointF(10, 10), actions, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(target, drop)
    return bool(enter.isAccepted()), bool(drop.isAccepted()), drop.dropAction()


@pytest.mark.parametrize('area', ['window', 'source', 'preview', 'preview_child', 'tabs', 'directory', 'find'])
def test_files_open_from_all_window_areas(window, tmp_path, area):
    window.show_find()
    window.new_document('txt')
    window.editor.insertPlainText('unsaved draft')
    draft = window.current_session
    path = tmp_path / '中文 #100%.JSON'
    path.write_text('{"name": "中文"}', encoding='utf-8')
    wait_until(lambda: window._preview_ready)
    targets = {'window': window, 'source': window.editor.viewport(), 'preview': window.preview,
               'preview_child': window.preview.focusProxy(), 'tabs': window.document_tabs,
               'directory': window.outline.viewport(), 'find': window.find_input}
    entered, dropped, action = drop_urls(targets[area], [QUrl.fromLocalFile(str(path))])
    assert entered, area
    assert dropped
    assert action == Qt.CopyAction
    wait_until(lambda: window.path == path.resolve())
    assert window.document_tabs.count() == 2
    assert window.editor.source_text() == '{"name": "中文"}'
    assert window.new_document_format == 'json'
    assert not window.editor.document().isModified()
    assert draft.document.isModified()
    window.document_tabs.setCurrentIndex(0)
    assert window.editor.source_text() == 'unsaved draft'
    assert path.exists()


def test_drop_multiple_formats_skips_folders_and_accepts_any_text_suffix(window, tmp_path):
    urls = [QUrl.fromLocalFile(str(tmp_path)), QUrl('https://example.com/remote.md')]
    formats = ['md', 'txt', 'json', 'xml', 'sql', 'markdown', 'mdown', 'mkd']
    files = []
    for index, suffix in enumerate(formats):
        path = tmp_path / ('file.' + suffix)
        path.write_text('content ' + str(index), encoding='utf-8')
        files.append(path)
        urls.append(QUrl.fromLocalFile(str(path)))
    image = tmp_path / 'image.png'
    image.write_bytes(b'not a document')
    files.append(image)
    urls.append(QUrl.fromLocalFile(str(image)))
    entered, dropped, _ = drop_urls(window, urls)
    assert entered and dropped
    wait_until(lambda: window.document_tabs.count() == len(files))
    assert [s.path for s in window.sessions] == [p.resolve() for p in files]
    assert window.path == files[-1].resolve()


def test_drop_existing_modified_file_keeps_draft_and_uses_same_tab(window, tmp_path, monkeypatch):
    prompts = []
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: prompts.append(args) or QMessageBox.No)
    path = tmp_path / 'note.md'
    path.write_text('# disk', encoding='utf-8')
    window.open_document_path(path)
    window.editor.selectAll()
    window.editor.insertPlainText('# modified')
    draft = window.current_session
    window.new_document('txt')
    entered, dropped, _ = drop_urls(window.preview, [QUrl.fromLocalFile(str(path))] * 2)
    assert entered and dropped
    wait_until(lambda: len(prompts) == 2)
    window.document_tabs.setCurrentIndex(window._session_tab_index(draft))
    assert window.document_tabs.count() == 2
    assert window.editor.source_text() == '# modified'
    assert window.editor.document().isModified()
    assert path.read_text(encoding='utf-8') == '# disk'


def test_drop_decode_failure_reports_chinese_error_and_opens_remaining_files(window, tmp_path, monkeypatch):
    broken = tmp_path / 'broken.txt'
    broken.write_bytes(b'\xff\xfe\xff')
    good = tmp_path / 'good.sql'
    good.write_text('select 1;', encoding='utf-8')
    messages = []
    monkeypatch.setattr(QMessageBox, 'question', lambda parent, title, text, *args: messages.append((title, text)) or QMessageBox.No)
    entered, dropped, _ = drop_urls(window, [QUrl.fromLocalFile(str(broken)), QUrl.fromLocalFile(str(good))])
    assert entered and dropped
    wait_until(lambda: window.path == good.resolve())
    assert len(messages) == 1 and messages[0][0] == '使用系统程序打开'
    assert window.document_tabs.count() == 1


@pytest.mark.parametrize('kind', ['directory', 'remote', 'missing'])
def test_non_document_drops_do_not_navigate_preview_or_change_document(window, tmp_path, kind):
    window.new_document('txt')
    window.editor.setPlainText('keep this')
    wait_until(lambda: window._preview_ready)
    binary = tmp_path / 'app.exe'
    binary.write_bytes(b'MZ')
    url = {'directory': QUrl.fromLocalFile(str(tmp_path)), 'remote': QUrl('https://example.com/a.md'),
           'missing': QUrl.fromLocalFile(str(tmp_path / 'missing.txt')),
           'binary': QUrl.fromLocalFile(str(binary))}[kind]
    original_url = window.preview.url()
    entered, dropped, _ = drop_urls(window.preview, [url])
    assert not entered and not dropped
    QTest.qWait(30)
    assert window.editor.source_text() == 'keep this'
    assert window.document_tabs.count() == 1
    assert window.preview.url() == original_url


def test_plain_text_drag_still_inserts_text_in_editor(window):
    window.new_document('txt')
    mime = QMimeData()
    mime.setText('dragged text')
    target = window.editor.viewport()
    enter = QDragEnterEvent(QPoint(70, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(target, enter)
    assert enter.isAccepted()
    drop = QDropEvent(QPointF(70, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(target, drop)
    assert drop.isAccepted()
    assert window.editor.source_text() == 'dragged text'
    assert window.document_tabs.count() == 1


def test_move_only_file_drag_is_not_opened_or_moved(window, tmp_path):
    path = tmp_path / 'keep-original.txt'
    path.write_text('keep', encoding='utf-8')
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    enter = QDragEnterEvent(QPoint(10, 10), Qt.MoveAction, mime, Qt.LeftButton, Qt.ShiftModifier)
    QApplication.sendEvent(window, enter)
    assert not enter.isAccepted()
    drop = QDropEvent(QPointF(10, 10), Qt.MoveAction, mime, Qt.LeftButton, Qt.ShiftModifier)
    QApplication.sendEvent(window, drop)
    QTest.qWait(40)
    assert path.read_text(encoding='utf-8') == 'keep'
    assert window.document_tabs.count() == 0


def test_file_drop_filter_does_not_intercept_dialog_controls(window, tmp_path):
    dialog = QDialog(window)
    layout = QVBoxLayout(dialog)
    edit = QLineEdit()
    layout.addWidget(edit)
    dialog.show()
    path = tmp_path / 'not-opened.md'
    path.write_text('# Not opened', encoding='utf-8')
    drop_urls(edit, [QUrl.fromLocalFile(str(path))])
    QTest.qWait(20)
    assert window.document_tabs.count() == 0
    dialog.close()
