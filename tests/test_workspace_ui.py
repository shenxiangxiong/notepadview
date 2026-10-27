from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeWidget, QToolButton, QMessageBox

from test_ui import app, window  # noqa: F401


def test_files_are_children_of_categories_without_close_buttons(window):
    window.new_document('md')
    window.new_document('json')
    assert isinstance(window.file_list, QTreeWidget)
    root = window.file_list.topLevelItem(0)
    assert root.text(0) == '未分类'
    assert root.childCount() == 2
    assert root.child(0).text(0).startswith('1. ')
    assert not window.file_list.findChildren(QToolButton, 'fileCloseButton')


def test_category_move_preserves_documents_and_delete_returns_to_uncategorized(window):
    window.new_document('txt')
    window.editor.insertPlainText('draft')
    session = window.current_session
    category = window.create_category('工作')
    assert category
    window.move_sessions_to_category([session], category)
    assert session.category_id == category
    item = window.file_list.item_for_session(session)
    assert item.parent().text(0) == '工作'
    assert window.editor.toPlainText() == 'draft'
    assert window.rename_category(category, '资料')
    assert item.parent().text(0) == '资料'
    window.delete_category(category)
    assert session.category_id == ''
    assert window.file_list.item_for_session(session).parent().text(0) == '未分类'


def test_context_menu_has_remove_batch_and_classify(window):
    window.new_document('sql')
    item = window.file_list.item_for_session(window.current_session)
    menu = window.file_list.build_context_menu(item)
    names = [action.text() for action in menu.actions()]
    assert '移除' in names
    assert '批量选择' in names
    assert '归类' in names


def test_session_restore_keeps_drafts_and_does_not_write_original(window, app, tmp_path):
    path = tmp_path / 'original.txt'
    path.write_text('original', encoding='utf-8')
    window.open_document_path(path)
    window.editor.insertPlainText(' changed')
    saved = window.current_session
    category = window.create_category('工作')
    window.move_sessions_to_category([saved], category)
    window.new_document('json')
    window.editor.insertPlainText('{"未保存": true}')
    active_id = window.current_session.id
    window.editor.set_selection(5, 5)
    assert window.flush_workspace()
    reopened = type(window)(settings=window.settings)
    try:
        assert len(reopened.sessions) == 2
        assert reopened.current_session.id == active_id
        assert reopened.editor.toPlainText() == '{"未保存": true}'
        assert reopened.editor.document().isModified()
        restored = next(session for session in reopened.sessions if session.path == path)
        assert restored.category_id == category
        assert restored.document.isModified()
        reopened.document_tabs.setCurrentIndex(reopened._session_tab_index(restored))
        assert 'changed' in reopened.editor.toPlainText()
        assert path.read_text(encoding='utf-8') == 'original'
    finally:
        reopened.close()


def test_remove_only_removes_workspace_entry(window, tmp_path):
    path = tmp_path / 'keep.txt'
    path.write_text('keep', encoding='utf-8')
    window.open_document_path(path)
    assert window.remove_sessions([window.current_session])
    assert not window.sessions
    assert path.read_text(encoding='utf-8') == 'keep'
    window.flush_workspace()
    reopened = type(window)(settings=window.settings)
    try:
        assert not reopened.sessions
    finally:
        reopened.close()


def test_find_dialog_has_only_native_close(window):
    assert not hasattr(window, 'find_close_button')


def test_exit_preserves_dirty_text_without_save_prompt(window, monkeypatch):
    window.new_document('txt')
    window.editor.insertPlainText('keep my draft')
    monkeypatch.setattr(window, 'confirm_discard', lambda: (_ for _ in ()).throw(AssertionError('exit must cache')))
    assert window.close()


def test_cache_failure_prevents_exit(window, monkeypatch):
    window.new_document('txt')
    window.editor.insertPlainText('important')
    monkeypatch.setattr(window, 'flush_workspace', lambda: False)
    assert not window.close()


def test_batch_category_selection_and_move(window):
    window.new_document('txt')
    window.new_document('sql')
    first, second = window.sessions
    category = window.create_category('资料')
    parent = window.file_list.item_for_session(first).parent()
    window.file_list._select_category_files(parent)
    assert {session.id for session in window.file_list.selected_sessions()} == {first.id, second.id}
    window.move_sessions_to_category(window.file_list.selected_sessions(), category)
    assert window.file_list.item_for_session(first).parent().childCount() == 2
    assert not window.rename_category(category, '未分类')


def test_unchanged_snapshot_reuses_text_without_reading_scintilla(window, monkeypatch):
    window.new_document('json')
    window.editor.insertPlainText('not json')
    first = window.workspace.snapshot()
    monkeypatch.setattr(window.current_session.document, 'get_char_range',
                        lambda *_: (_ for _ in ()).throw(AssertionError('unchanged text must be cached')))
    window.editor.set_selection(2, 2)
    second = window.workspace.snapshot()
    assert first['documents'][0]['text'] == second['documents'][0]['text']
    assert second['documents'][0]['selection']['ranges'][0][1] == 2


def test_automatic_cache_saves_open_draft(window):
    from test_ui import wait_until
    window.new_document('xml')
    window.editor.insertPlainText('<root>草稿</root>')
    wait_until(lambda: window.workspace.store.path.is_file())
    state = window.workspace.store.load()
    assert state['documents'][0]['text'] == '<root>草稿</root>'
    assert state['documents'][0]['modified']


def test_dirty_remove_can_be_cancelled(window, monkeypatch):
    window.new_document('txt')
    window.editor.insertPlainText('draft')
    session = window.current_session
    monkeypatch.setattr(QMessageBox, 'warning', lambda *_: QMessageBox.No)
    assert not window.remove_sessions([session])
    assert window.sessions == [session]
    monkeypatch.setattr(QMessageBox, 'warning', lambda *_: QMessageBox.Yes)
    assert window.remove_sessions([session])


def test_failed_remove_preserves_live_session_and_cached_content(window, monkeypatch):
    window.new_document('txt')
    window.editor.insertPlainText('do not lose')
    session = window.current_session
    assert window.flush_workspace()
    monkeypatch.setattr(window.workspace.store, 'save', lambda *_: (_ for _ in ()).throw(OSError('disk full')))
    monkeypatch.setattr(QMessageBox, 'warning', lambda *_: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, 'critical', lambda *_: QMessageBox.Ok)
    assert not window.remove_sessions([session])
    assert window.current_session is session
    assert session in window.sessions
    assert window.editor.toPlainText() == 'do not lose'


def test_keyboard_navigation_activates_document(window):
    from PySide6.QtTest import QTest
    window.new_document('txt')
    window.editor.setPlainText('one')
    first = window.current_session
    window.new_document('sql')
    window.editor.setPlainText('two')
    window.file_list.setCurrentItem(window.file_list.item_for_session(first))
    assert window.editor.toPlainText() == 'one'
    window.file_list.setFocus()
    QTest.keyClick(window.file_list, Qt.Key_Down)
    assert window.editor.toPlainText() == 'two'


def test_files_remain_in_open_order_after_reclassification(window):
    for language in ('md', 'txt', 'sql'):
        window.new_document(language)
    first, second, third = window.sessions
    category = window.create_category('资料')
    window.move_sessions_to_category([third], category)
    window.move_sessions_to_category([first], category)
    parent = window.file_list.item_for_session(first).parent()
    assert [parent.child(i).data(0, Qt.UserRole).id for i in range(parent.childCount())] == [first.id, third.id]


def test_typing_after_dirty_transition_does_not_refresh_whole_tree(window, monkeypatch):
    window.new_document('txt')
    window.editor.insertPlainText('first')
    calls = []
    monkeypatch.setattr(window.file_list, 'refresh', lambda *_: calls.append(True))
    window.editor.insertPlainText('second')
    assert calls == []
