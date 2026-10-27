from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPoint, QUrl, Qt
from PySide6.QtGui import QDragEnterEvent, QImage, QPdfWriter, QPainter
from PySide6.QtWidgets import QApplication, QMessageBox

from notepadview.document import read_document
from test_ui import app, window  # noqa: F401


@pytest.mark.parametrize('encoding', ['utf-8-sig', 'utf-16', 'utf-32', 'utf-16-be', 'utf-32-be', 'gb18030'])
def test_common_encoding_roundtrip(window, tmp_path, encoding):
    path = tmp_path / '中文.config'
    content = '中文配置=测试\r\n第二行\r\n'
    original = ('\ufeff' + content if encoding.endswith('-be') else content).encode(encoding)
    path.write_bytes(original)
    assert window.open_document_path(path)
    assert window.editor.source_text() == '中文配置=测试\n第二行\n'
    assert window.save_document()
    assert path.read_bytes() == original
    window.editor.insertPlainText('新增')
    assert window.save_document()
    assert path.read_bytes().decode(encoding).lstrip('\ufeff').startswith('新增中文')


def test_mz_prefix_does_not_reject_valid_text(tmp_path):
    path = tmp_path / 'settings.config'
    path.write_bytes(b'MZ=value\n')
    assert read_document(path) == 'MZ=value\n'


def test_export_cannot_overwrite_open_preview(window, tmp_path, monkeypatch):
    path = image_file(tmp_path)
    original = path.read_bytes()
    assert window.open_document_path(path)
    window.new_document('md')
    window.editor.setPlainText('# text')
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: QMessageBox.Ok)
    assert not window.export_to(path)
    assert path.read_bytes() == original


def test_preview_rename_keeps_new_path_when_external_file_is_corrupted(window, tmp_path):
    path = image_file(tmp_path)
    assert window.open_document_path(path)
    session = window.current_session
    path.write_bytes(b'corrupted image')
    assert window.rename_document_session(session, 'renamed')
    assert session.path == path.with_name('renamed.data')
    assert session.path.exists() and not path.exists()
    assert window.is_readonly_preview
    assert '预览不可用' in window.binary_preview.caption.text()
    assert not window.save_to(session.path)


@pytest.mark.parametrize('ansi_codec,expected_encoding', [
    ('cp936', 'ansi'),
    ('utf-8', 'gb18030'),
])
def test_encoding_restore_and_unrepresentable_save_preserve_original(
        window, tmp_path, monkeypatch, ansi_codec, expected_encoding):
    from notepadview import document
    monkeypatch.setattr(document, 'ansi_encoding', lambda: ansi_codec)
    path = tmp_path / 'legacy.conf'
    original = '中文\r\n'.encode('gb18030')
    path.write_bytes(original)
    assert window.open_document_path(path)
    assert window.current_session.encoding == expected_encoding
    assert window.flush_workspace()
    restored = type(window)(settings=window.settings)
    try:
        assert restored.current_session.encoding == expected_encoding
        assert restored.editor.source_text() == '中文\n'
        assert restored.current_session.newline == '\r\n'
        assert restored.save_document()
        assert path.read_bytes() == original
        with pytest.raises(UnicodeEncodeError):
            document.save_document(path, 'invalid surrogate \ud800', encoding=expected_encoding)
        assert path.read_bytes() == original
        restored.editor.document().setModified(False)
    finally:
        restored.close()


def test_missing_preview_restore_stays_readonly(window, tmp_path):
    path = image_file(tmp_path)
    assert window.open_document_path(path)
    assert window.flush_workspace()
    path.unlink()
    restored = type(window)(settings=window.settings)
    try:
        assert restored.is_readonly_preview
        assert '预览不可用' in restored.binary_preview.caption.text()
        assert not restored.save_to(path)
        assert not path.exists()
    finally:
        restored.close()


def test_binary_never_read_as_text(tmp_path):
    path = tmp_path / 'payload.txt'
    path.write_bytes(b'MZ\x00\x01\xff\x00payload')
    with pytest.raises(ValueError):
        read_document(path)


def test_unknown_extension_and_extensionless_text(window, tmp_path):
    for name in ['config.custom', 'LICENSE']:
        path = tmp_path / name
        path.write_text('plain text 中文', encoding='utf-8')
        assert window.open_document_path(path)
        assert window.current_session.document_format == 'txt'
        assert window.editor.source_text() == 'plain text 中文'


def image_file(tmp_path):
    path = tmp_path / 'picture.data'
    image = QImage(80, 40, QImage.Format_RGB32)
    image.fill(Qt.red)
    assert image.save(str(path), 'PNG')
    return path


def pdf_file(tmp_path):
    path = tmp_path / 'pages.pdf'
    writer = QPdfWriter(str(path))
    painter = QPainter(writer)
    painter.drawText(100, 100, 'Page one')
    writer.newPage()
    painter.drawText(100, 100, 'Page two')
    painter.end()
    del writer
    return path


@pytest.mark.parametrize('factory,kind', [(image_file, 'image'), (pdf_file, 'pdf')])
def test_native_preview_protected_and_switchable(window, tmp_path, factory, kind, monkeypatch):
    def unexpected_message(_parent, title, text, *args):
        pytest.fail(title + ': ' + text)
    monkeypatch.setattr(QMessageBox, 'warning', unexpected_message)
    monkeypatch.setattr(QMessageBox, 'critical', unexpected_message)
    window.new_document('json')
    window.editor.insertPlainText('{"keep":1}')
    text_session = window.current_session
    path = factory(tmp_path)
    original = path.read_bytes()
    assert window.open_document_path(path)
    session = window.current_session
    assert session.kind == kind
    assert window.binary_preview.isVisible()
    assert not window.editor_split.isVisible()
    assert window.editor.readOnly()
    for action in [window.save_action, window.save_as_action, window.export_action,
                   window.find_action, window.format_document_action, *window.format_actions]:
        assert not action.isEnabled()
    assert window.save_document() is False
    assert window.save_as() is False
    assert window.save_to(path) is False
    assert window.export_to(path) is False
    window.wrap_selection('**', '**', 'unsafe')
    window.replace_all()
    assert not session.document.isModified()
    assert path.read_bytes() == original
    preview = window.binary_preview
    preview.zoom(1.25)
    if kind == 'pdf':
        assert preview.pdf_document.pageCount() == 2
        preview.page_number.setValue(2)
        assert preview.pdf_view.pageNavigator().currentPage() == 1
    preview.fit()
    window.set_mode('source')
    assert preview.isVisible()
    window.document_tabs.setCurrentIndex(window._session_tab_index(text_session))
    assert not preview.isVisible()
    assert window.editor_split.isVisible()
    assert not window.editor.readOnly()
    assert window.editor.source_text() == '{"keep":1}'
    assert window.save_action.isEnabled()
    window.document_tabs.setCurrentIndex(window._session_tab_index(session))
    assert window.rename_document_session(session, 'renamed')
    assert session.path.read_bytes() == original
    assert window.flush_workspace()
    reopened = type(window)(settings=window.settings)
    try:
        assert reopened.current_session.kind == kind
        assert reopened.binary_preview.isVisibleTo(reopened)
        assert reopened.save_to(session.path) is False
    finally:
        reopened.close()
    assert window.close_document_session(session)
    assert window.current_session is text_session


def test_binary_rejected_without_system_default_or_session(window, tmp_path, monkeypatch):
    from PySide6.QtGui import QDesktopServices
    path = tmp_path / 'archive.unknown'
    original = b'PK\x03\x04\x00\xff\x00'
    path.write_bytes(original)
    opened = []
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.Yes)
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: opened.append(url) or True)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: QMessageBox.Ok)
    assert not window.open_document_path(path)
    assert not opened
    assert not window.sessions
    assert path.read_bytes() == original


def test_repeat_preview_open_requires_confirmation(window, tmp_path, monkeypatch):
    path = image_file(tmp_path)
    assert window.open_document_path(path)
    prompts = []
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: prompts.append(args) or QMessageBox.No)
    assert not window.open_document_path(path)
    assert len(prompts) == 1 and len(window.sessions) == 1
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.Yes)
    assert window.open_document_path(path)
    assert len(window.sessions) == 1


def test_drag_accepts_arbitrary_local_file(window, tmp_path):
    path = tmp_path / 'config.ini'
    path.write_text('key=value', encoding='utf-8')
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    event = QDragEnterEvent(QPoint(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(window, event)
    assert event.isAccepted()
    window.file_drop_filter.open_files([path])
    assert window.editor.source_text() == 'key=value'
