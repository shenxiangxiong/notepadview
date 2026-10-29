import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from shiboken6 import isValid

from test_ui import app, window  # noqa: F401


def visual_sessions(window):
    return [window.file_list.topLevelItem(i).child(j).data(0, Qt.UserRole)
            for i in range(window.file_list.topLevelItemCount())
            for j in range(window.file_list.topLevelItem(i).childCount())]


def assert_numbers(window):
    for row in range(window.file_list.topLevelItemCount()):
        category = window.file_list.topLevelItem(row)
        for index in range(category.childCount()):
            item = category.child(index)
            session = item.data(0, Qt.UserRole)
            assert item.text(0).startswith(f'{index + 1}. ')
            assert window.document_tabs.tabText(window._session_tab_index(session)) == item.text(0)


@pytest.mark.parametrize('operation', ['batch', 'current', 'other', 'close'])
def test_visual_numbering_after_removal(window, operation):
    for kind in ('md', 'txt', 'json', 'sql'):
        window.new_document(kind, category_id='')
    first, second, third, fourth = window.sessions
    category = window.create_category('分类')
    window.move_sessions_to_category([first, third], category)
    assert_numbers(window)
    if operation == 'close':
        assert window.close_document_tab(window._session_tab_index(second))
    else:
        targets = {'batch': [first, third], 'current': [fourth], 'other': [second]}[operation]
        assert window.remove_sessions(targets)
    assert_numbers(window)


def test_empty_duplicate_and_batch_removal_release_every_document(window, monkeypatch):
    assert window.remove_sessions([])
    for _ in range(3):
        window.new_document('txt')
    targets = list(window.sessions)
    released = []
    release = window._release_diagram
    def track_release(session):
        released.append(session.id)
        release(session)
    monkeypatch.setattr(window, '_release_diagram', track_release)
    assert window.remove_sessions([*targets, targets[0]])
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert released == [session.id for session in targets]
    assert all(not isValid(session.document) for session in targets)
    assert not window.sessions
    assert window.current_session is None


def test_each_document_returns_to_source_after_switch_and_restart(window):
    window.set_mode('source')
    window.new_document('md')
    first = window.current_session
    window.set_mode('preview')
    window.set_mode('split')
    window.new_document('txt')
    second = window.current_session
    assert window.mode == 'source'
    window.document_tabs.setCurrentIndex(window._session_tab_index(first))
    assert window.mode == 'source'
    window.toggle_split()
    assert window.mode == 'split'
    window.toggle_split()
    assert window.mode == 'source'
    window.set_mode('preview')
    window.show_find()
    assert window.mode == 'preview'
    window.hide_find()
    assert first.view_mode == 'source'
    assert window.flush_workspace()
    reopened = type(window)(settings=window.settings)
    try:
        assert reopened.mode == 'source'
        reopened.document_tabs.setCurrentIndex(reopened._session_tab_index(next(s for s in reopened.sessions if s.id == second.id)))
        assert reopened.mode == 'source'
        reopened.document_tabs.setCurrentIndex(reopened._session_tab_index(next(s for s in reopened.sessions if s.id == first.id)))
        assert reopened.mode == 'source'
    finally:
        reopened.close()


def test_legacy_workspace_uses_default_view_and_sqlite(window):
    from pathlib import Path
    from test_workspace_store import _state, _write
    window.set_mode('preview')
    legacy = Path(window.settings.fileName() + '.workspace.json')
    _write(legacy, _state())
    before = legacy.read_bytes()
    reopened = type(window)(settings=window.settings)
    try:
        assert reopened.mode == 'source'
        assert reopened.current_session.view_mode == 'source'
        assert reopened.workspace.store.path.suffix == '.sqlite3'
        assert reopened.flush_workspace()
        assert legacy.read_bytes() == before
    finally:
        reopened.close()


def test_binary_preview_does_not_pollute_text_mode(window, tmp_path):
    from PySide6.QtGui import QImage
    window.new_document('txt')
    text = window.current_session
    window.set_mode('source')
    path = tmp_path / 'picture.png'
    QImage(2, 2, QImage.Format_RGB32).save(str(path))
    assert window.open_document_path(path)
    assert window.mode == 'preview'
    window.set_mode('split')
    window.document_tabs.setCurrentIndex(window._session_tab_index(text))
    assert window.mode == 'source'


def test_category_move_delete_and_tab_reorder_keep_local_numbers(window):
    for _ in range(5):
        window.new_document('txt', category_id='')
    first, second, third, fourth, fifth = window.sessions
    category = window.create_category('numbering')
    window.move_sessions_to_category([first, third, fifth], category)
    assert_numbers(window)
    window.document_tabs.moveTab(window._session_tab_index(fifth), 0)
    window.refresh_file_list()
    assert_numbers(window)
    window.move_sessions_to_category([third], '')
    assert_numbers(window)
    window.delete_category(category)
    assert_numbers(window)
