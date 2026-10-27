import pytest
from PySide6.QtWidgets import QMessageBox

from notepadview.window import MainWindow
from test_ebook_reader import write_epub
from test_ui import app, wait_until, window  # noqa: F401


@pytest.mark.parametrize('suffix', ['.epub', '.mobi', '.azw3', '.fb2'])
def test_ebook_formats_open_as_readonly_reader_sessions(
        window, tmp_path, monkeypatch, suffix):
    jobs = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: QMessageBox.Ok)
    monkeypatch.setattr(
        window, '_start_reader_job',
        lambda session, operation: jobs.append((session, operation)),
        raising=False,
    )
    path = tmp_path / ('sample' + suffix)
    original = b'ebook source bytes'
    path.write_bytes(original)

    assert window.open_document_path(path)
    session = window.current_session
    assert session.kind == 'ebook'
    assert session.path == path.resolve()
    assert window.editor.readOnly()
    assert not window.save_action.isEnabled()
    assert not window.save_as_action.isEnabled()
    assert not window.undo_action.isEnabled()
    assert not window.redo_action.isEnabled()
    assert not window.save_to(tmp_path / ('copy' + suffix))
    assert len(jobs) == 1 and jobs[0][0] is session
    assert path.read_bytes() == original


def test_ebook_reader_state_restores_with_workspace(window, tmp_path, monkeypatch):
    jobs = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: QMessageBox.Ok)
    monkeypatch.setattr(
        MainWindow, '_start_reader_job',
        lambda self, session, operation: jobs.append((session, operation)),
    )
    path = tmp_path / 'sample.epub'
    path.write_bytes(b'ebook source bytes')

    assert window.open_document_path(path)
    session = window.current_session
    session.reader_state = {'chapter': 3, 'scroll': 420.0, 'zoom': 1.4}
    assert window.flush_workspace()

    restored = MainWindow(settings=window.settings)
    try:
        assert restored.workspace.load_error is None
        assert restored.current_session.kind == 'ebook'
        assert restored.current_session.reader_state == session.reader_state
        assert len(jobs) == 2
    finally:
        restored.close()


def test_epub_open_loads_book_in_background(window, tmp_path):
    path = tmp_path / 'sample.epub'
    write_epub(path)

    assert window.open_document_path(path)
    session = window.current_session
    assert session.kind == 'ebook'
    try:
        wait_until(lambda: session.ebook_widget.book is not None or
                   session.reader_error is not None)
    except AssertionError as error:
        pytest.fail(f'{error}; jobs={window._reader_jobs}, '
                    f'active={window.reader_pool.activeThreadCount()}, '
                    f'generation={session.reader_generation}, '
                    f'widget={session.ebook_widget!r}')
    assert session.reader_error is None
    assert session.ebook_widget.book.title == '测试书'
    assert session.ebook_widget.toc_list.count() == 2


def test_invalid_epub_shows_reader_error_without_text_fallback(window, tmp_path):
    path = tmp_path / 'broken.epub'
    path.write_bytes(b'not an epub')

    assert window.open_document_path(path)
    session = window.current_session
    assert session.kind == 'ebook'
    assert window.editor.readOnly()
    try:
        wait_until(lambda: session.reader_error is not None)
    except AssertionError as error:
        pytest.fail(f'{error}; jobs={window._reader_jobs}, '
                    f'active={window.reader_pool.activeThreadCount()}, '
                    f'generation={session.reader_generation}')
    assert 'EPUB' in session.reader_error
    assert session.ebook_widget.book is None
