import errno
import json
from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog, QInputDialog, QMessageBox

from notepadview.dialogs import NewDocumentDialog
from notepadview.workspace_store import WorkspaceStore
from test_ui import app, window  # noqa: F401
from test_workspace_store import _state


@pytest.fixture
def messages(monkeypatch):
    result = []

    def record(_parent, title, text, *_args, **_kwargs):
        result.append((title, text))
        return QMessageBox.Ok

    monkeypatch.setattr(QMessageBox, 'warning', record)
    monkeypatch.setattr(QMessageBox, 'critical', record)
    return result


def _action(menu, text):
    action = next((action for action in menu.actions() if action.text() == text), None)
    assert action is not None, f'missing context action: {text}'
    return action


def test_draft_rename_preserves_document_and_refreshes_surfaces(window):
    window.new_document('json')
    window.editor.insertPlainText('{"draft":true}')
    window.editor.set_selection(2, 7)
    session = window.current_session
    document = session.document
    before = window.workspace.snapshot()['documents'][0]
    assert window.rename_document_session(session, '接口草稿')
    assert session.name == '接口草稿.json'
    assert session.display_name == '接口草稿.json'
    assert session.document is document
    assert session.path is None
    assert window.workspace.snapshot()['documents'][0] == dict(before, name='接口草稿.json')
    assert window.editor.toPlainText() == '{"draft":true}'
    assert document.isModified()
    assert '接口草稿.json' in window.windowTitle()
    assert '接口草稿.json' in window.document_tabs.tabText(0)
    assert '接口草稿.json' in window.document_tabs.tabToolTip(0)
    assert window.side_filename.text() == '接口草稿.json'
    assert '接口草稿.json' in window.path_label.text()
    assert '接口草稿.json' in window.file_list.item_for_session(session).text(0)
    assert '重命名' in window.statusBar().currentMessage()


def test_named_drafts_restore_all_tab_labels_and_selection(window):
    for language, name in [('md', '笔记'), ('txt', '备忘')]:
        window.new_document(language)
        window.editor.insertPlainText(name)
        window.editor.set_selection(0, 2)
        assert window.rename_document_session(window.current_session, name)
    expected = window.workspace.snapshot()
    assert window.flush_workspace()
    reopened = type(window)(settings=window.settings)
    try:
        assert json.loads(json.dumps(reopened.workspace.snapshot())) == json.loads(json.dumps(expected))
        assert [session.name for session in reopened.sessions] == ['笔记.md', '备忘.txt']
        for index, session in enumerate(reopened._tab_sessions()):
            assert session.name in reopened.document_tabs.tabText(index)
            assert reopened.document_tabs.tabToolTip(index) == session.name
            assert session.name in reopened.file_list.item_for_session(session).text(0)
        assert '备忘.txt' in reopened.windowTitle()
    finally:
        reopened.close()


def test_legacy_cache_without_name_restores_default_and_saved_names(window, tmp_path):
    state = _state()
    state['documents'].append(dict(state['documents'][0], id='saved',
                                   path=str(tmp_path / 'saved.markdown')))
    window.workspace.store.save(state)
    reopened = type(window)(settings=window.settings)
    try:
        assert reopened.workspace.load_error is None
        assert [session.name for session in reopened.sessions] == ['未命名.md', 'saved.markdown']
        assert [session.display_name for session in reopened.sessions] == ['', '']
        assert 'saved.markdown' in reopened.document_tabs.tabText(1)
    finally:
        reopened.close()


@pytest.mark.parametrize('invalid', [None, True, 1, [], {}])
def test_cache_optional_name_requires_string_and_preserves_cache(tmp_path, invalid):
    store = WorkspaceStore(tmp_path / 'cache.json')
    original = _state()
    store.save(original)
    before = store.path.read_bytes()
    invalid_state = _state()
    invalid_state['documents'][0]['name'] = invalid
    with pytest.raises(ValueError, match='name'):
        store.save(invalid_state)
    assert store.path.read_bytes() == before
    assert store.load() == original


@pytest.mark.parametrize('name', ['', '中文草稿.md'])
def test_optional_cache_name_roundtrip(tmp_path, name):
    state = _state()
    state['documents'][0]['name'] = name
    store = WorkspaceStore(tmp_path / 'cache.json')
    store.save(state)
    assert store.load() == state


def test_saved_dirty_rename_moves_original_bytes_and_preserves_live_document(window, tmp_path):
    source = tmp_path / 'original.markdown'
    original = b'# original\r\nuntouched\r\n'
    source.write_bytes(original)
    window.open_document_path(source)
    window.editor.insertPlainText('unsaved ')
    window.editor.set_selection(1, 6)
    session = window.current_session
    category = window.create_category('工作')
    window.move_sessions_to_category([session], category)
    before = window.workspace.snapshot()['documents'][0]
    document = session.document
    revision = document.revision
    assert window.rename_document_session(session, 'renamed')
    target = tmp_path / 'renamed.markdown'
    assert not source.exists()
    assert target.read_bytes() == original
    assert session.path == window.path == target
    assert session.name == target.name
    assert session.document is document
    assert document.revision == revision
    assert document.isModified() and document.isUndoAvailable()
    after = window.workspace.snapshot()['documents'][0]
    assert after == dict(before, path=str(target))
    assert session.category_id == category
    assert 'renamed.markdown' in window.windowTitle()
    assert str(target) in window.path_label.text()
    assert window.file_list.item_for_session(session).toolTip(0) == str(target)
    assert str(source) not in window.recent
    assert window.recent[0] == str(target)
    assert window.settings.value('recent', type=list) == window.recent
    assert window.recent_menu.actions()[0].text() == target.name
    assert window.flush_workspace()
    reopened = type(window)(settings=window.settings)
    try:
        assert reopened.workspace.snapshot() == window.workspace.snapshot()
        assert reopened.current_session.path == target
        assert reopened.current_session.document.isModified()
        assert target.read_bytes() == original
    finally:
        reopened.close()


def test_rename_inactive_session_does_not_switch_document_or_selection(window, tmp_path):
    source = tmp_path / 'first.txt'
    source.write_text('original', encoding='utf-8')
    window.open_document_path(source)
    first = window.current_session
    window.editor.insertPlainText('draft')
    window.editor.set_selection(1, 4)
    window.new_document('sql')
    window.editor.insertPlainText('select 1;')
    window.editor.set_selection(2, 5)
    current = window.current_session
    before = window.workspace.snapshot()
    assert window.rename_document_session(first, 'renamed.txt')
    assert window.current_session is current
    assert window.path is None
    assert window.editor.toPlainText() == 'select 1;'
    assert window.editor.selection_state() == before['documents'][1]['selection']
    assert first.selection == before['documents'][0]['selection']
    assert 'renamed.txt' in window.document_tabs.tabText(0)
    assert window.document_tabs.tabToolTip(0) == str(tmp_path / 'renamed.txt')
    assert 'renamed.txt' in window.file_list.item_for_session(first).text(0)


@pytest.mark.parametrize('collision', ['file', 'directory', 'open_missing'])
def test_rename_collision_preserves_disk_sessions_and_recent(window, tmp_path, messages, collision):
    source = tmp_path / 'source.txt'
    target = tmp_path / 'target.txt'
    source.write_bytes(b'original\r\n')
    if collision == 'directory':
        target.mkdir()
    else:
        target.write_bytes(b'never overwrite')
    if collision == 'open_missing':
        window.open_document_path(target)
        target.unlink()
    window.open_document_path(source)
    window.editor.insertPlainText('dirty')
    session = window.current_session
    before = window.workspace.snapshot()
    recent = list(window.recent)
    assert not window.rename_document_session(session, target.name)
    assert window.workspace.snapshot() == before
    assert window.recent == recent
    assert source.read_bytes() == b'original\r\n'
    if collision == 'file':
        assert target.read_bytes() == b'never overwrite'
    assert messages and '重命名' in messages[-1][0]
    assert any(word in messages[-1][1] for word in ('已存在', '已打开'))


def test_failed_filesystem_rename_keeps_all_live_and_cached_state(window, tmp_path, monkeypatch, messages):
    source = tmp_path / 'source.txt'
    source.write_bytes(b'original')
    window.open_document_path(source)
    window.editor.insertPlainText('dirty')
    window.editor.set_selection(1, 3)
    session = window.current_session
    before = window.workspace.snapshot()
    recent = list(window.recent)

    def fail(_source, _target):
        raise PermissionError(errno.EACCES, 'access denied')

    monkeypatch.setattr(Path, 'rename', fail)
    assert not window.rename_document_session(session, 'new.txt')
    assert window.workspace.snapshot() == before
    assert window.recent == recent
    assert source.read_bytes() == b'original'
    assert not (tmp_path / 'new.txt').exists()
    assert messages[-1][0] == '重命名失败'
    assert '权限' in messages[-1][1]
    assert 'access denied' not in messages[-1][1]


@pytest.mark.parametrize('name', ['', ' ', '.', '..', 'bad.', 'bad ', 'bad\t',
                                 'bad/name', 'bad\\name', 'bad:name', 'bad*name',
                                 'bad?name', 'bad"name', 'bad<name', 'bad>name',
                                 'bad|name', 'bad\x00name', 'bad\nname',
                                 'CON', 'prn.txt', 'AUX.note.txt', 'NUL',
                                 'COM1', 'lpt9.txt', 'COM¹.txt', 'CONIN$.txt'])
def test_invalid_windows_names_are_rejected_without_changes(window, messages, name):
    window.new_document('txt')
    before = window.workspace.snapshot()
    assert not window.rename_document_session(window.current_session, name)
    assert window.workspace.snapshot() == before
    assert messages and messages[-1][0] == '重命名失败'
    assert any('\u3400' <= character <= '\u9fff' for character in messages[-1][1])


@pytest.mark.parametrize('saved', [False, True])
def test_extension_change_requires_save_as(window, tmp_path, messages, saved):
    if saved:
        source = tmp_path / 'source.markdown'
        source.write_bytes(b'original')
        window.open_document_path(source)
    else:
        window.new_document('md')
    before = window.workspace.snapshot()
    assert not window.rename_document_session(window.current_session, 'changed.txt')
    assert window.workspace.snapshot() == before
    assert '另存为' in messages[-1][1]


def test_same_name_is_noop_and_clean_draft_stays_clean(window, monkeypatch, messages):
    window.new_document('txt')
    session = window.current_session
    assert window.rename_document_session(session, 'notes.txt')
    assert not session.document.isModified()
    before = window.workspace.snapshot()
    monkeypatch.setattr(window, 'schedule_workspace_save', lambda: pytest.fail('same name must do nothing'))
    assert window.rename_document_session(session, 'notes')
    assert window.workspace.snapshot() == before
    assert not messages


def test_saved_same_name_does_not_touch_filesystem(window, tmp_path, monkeypatch):
    path = tmp_path / 'source.txt'
    path.write_bytes(b'original')
    window.open_document_path(path)
    monkeypatch.setattr(Path, 'rename', lambda *_: pytest.fail('same name must not rename'))
    assert window.rename_document_session(window.current_session, path.name)
    assert path.read_bytes() == b'original'


def test_draft_name_conflicts_with_other_open_draft(window, messages):
    window.new_document('txt')
    assert window.rename_document_session(window.current_session, 'Notes.txt')
    window.new_document('txt')
    before = window.workspace.snapshot()
    assert not window.rename_document_session(window.current_session, 'notes.txt')
    assert window.workspace.snapshot() == before
    assert '已' in messages[-1][1]


def test_rename_request_has_explicit_chinese_dialog_and_current_fallback(window, monkeypatch):
    window.new_document('xml')
    session = window.current_session

    def accept(dialog):
        assert dialog.windowTitle() == '重命名文档'
        assert '文件名' in dialog.labelText()
        assert '.xml' in dialog.labelText()
        assert dialog.textValue() == '未命名.xml'
        assert dialog.okButtonText() == '重命名'
        assert dialog.cancelButtonText() == '取消'
        dialog.setTextValue('配置')
        return QDialog.Accepted

    monkeypatch.setattr(QInputDialog, 'exec', accept)
    assert window.request_rename_document()
    assert session.name == '配置.xml'


def test_rename_request_cancel_does_not_change_inactive_session(window, monkeypatch):
    window.new_document('md')
    first = window.current_session
    window.new_document('txt')
    before = window.workspace.snapshot()
    monkeypatch.setattr(QInputDialog, 'exec', lambda _: QDialog.Rejected)
    assert not window.request_rename_document(first)
    assert window.workspace.snapshot() == before


def test_rename_request_without_current_document_returns_false(window):
    assert not window.request_rename_document()


def test_file_context_menu_requests_rename_of_clicked_session(window, monkeypatch):
    window.new_document('md')
    first = window.current_session
    window.new_document('txt')
    calls = []
    monkeypatch.setattr(window, 'request_rename_document', lambda session=None: calls.append(session))
    menu = window.file_list.build_context_menu(window.file_list.item_for_session(first))
    action = next((a for a in menu.actions() if a.text().rstrip('…') == '重命名'), None)
    assert action is not None
    action.trigger()
    assert calls == [first]


@pytest.mark.parametrize('uncategorized', [False, True])
def test_category_context_new_document_uses_clicked_category(window, monkeypatch, uncategorized):
    active_category = window.create_category('当前分类')
    clicked_category = '' if uncategorized else window.create_category('目标分类')
    window.new_document('md', category_id=active_category)
    previous = window.current_session
    current_item = window.file_list.item_for_session(previous)
    assert window.file_list.currentItem() is current_item

    def accept(dialog):
        assert window.current_session is previous
        assert window.file_list.currentItem() is current_item
        dialog.type_list.setCurrentRow(4)
        return QDialog.Accepted

    monkeypatch.setattr(NewDocumentDialog, 'exec', accept)
    item = window.file_list._category_items[clicked_category]
    menu = window.file_list.build_context_menu(item)
    new_action = _action(menu, '新建')
    _action(new_action.menu(), '文件…').trigger()
    assert len(window.sessions) == 2
    assert window.current_session.category_id == clicked_category
    assert window.current_session.document_format == 'sql'
    assert window.file_list.item_for_session(window.current_session).parent() is item


def test_cancel_category_new_document_creates_nothing(window, monkeypatch):
    category = window.create_category('目标分类')
    before = window.workspace.snapshot()
    monkeypatch.setattr(NewDocumentDialog, 'exec', lambda _: QDialog.Rejected)
    item = window.file_list._category_items[category]
    menu = window.file_list.build_context_menu(item)
    new_action = _action(menu, '新建')
    _action(new_action.menu(), '文件…').trigger()
    assert window.workspace.snapshot() == before


@pytest.mark.parametrize('checked', [False, True])
def test_new_action_boolean_is_not_category_id(window, monkeypatch, checked):
    category = window.create_category('当前分类')
    window.file_list.setCurrentItem(window.file_list._category_items[category])
    monkeypatch.setattr(NewDocumentDialog, 'exec', lambda _: QDialog.Accepted)
    assert window.choose_new_document(checked)
    assert window.current_session.category_id == category
    assert isinstance(window.current_session.category_id, str)


def test_named_draft_is_suggested_in_save_as(window, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    window.new_document('txt')
    assert window.rename_document_session(window.current_session, '备忘')
    suggestions = []

    def cancel(_parent, _title, path, *_args, **_kwargs):
        suggestions.append(path)
        return '', ''

    monkeypatch.setattr(QFileDialog, 'getSaveFileName', cancel)
    assert not window.save_as()
    assert Path(suggestions[0]).name == '备忘.txt'


def test_rename_action_tracks_current_session(window):
    from PySide6.QtGui import QAction
    if not hasattr(window, 'rename_action'):
        window.rename_action = QAction('重命名…', window)
    window.update_title()
    assert not window.rename_action.isEnabled()
    window.new_document('txt')
    assert window.rename_action.isEnabled()
    assert window.close_document_tab(0)
    assert not window.rename_action.isEnabled()
