from pathlib import Path

import pytest
from PySide6.QtGui import QDesktopServices

from test_ui import app, window  # noqa: F401


@pytest.mark.parametrize('mode', ['source', 'preview'])
def test_split_button_returns_to_previous_single_view(window, mode):
    window.set_mode(mode)
    window.split_button.click()
    assert window.mode == 'split' and window.split_button.isChecked()
    window.split_button.click()
    assert window.mode == mode and not window.split_button.isChecked()
    assert window.settings.value('default_view') == mode


def test_previous_single_view_survives_restart_while_split_is_selected(window):
    window.set_mode('preview')
    window.split_button.click()
    reopened = type(window)(settings=window.settings)
    try:
        assert reopened.mode == 'split'
        reopened.split_button.click()
        assert reopened.mode == 'preview'
    finally:
        reopened.close()


def test_default_category_is_editable_persistent_and_cannot_be_removed(window):
    window.new_document('txt')
    session = window.current_session
    assert window.rename_category('', '我的文件')
    window.delete_category('')
    assert window.file_list.topLevelItemCount() >= 1
    assert window.file_list.item_for_session(session).parent().text(0) == '我的文件'
    assert not window.create_category('我的文件')
    assert not window.rename_category('', '  ')
    assert window.flush_workspace()
    reopened = type(window)(settings=window.settings)
    try:
        assert reopened.file_list.topLevelItem(0).text(0) == '我的文件'
        assert reopened.current_session.category_id == ''
    finally:
        reopened.close()


def test_default_category_rejects_existing_custom_name(window):
    window.create_category('工作')
    assert not window.rename_category('', '工作')


def test_file_menu_new_has_file_and_category_entries(window, monkeypatch):
    assert [action.text() for action in window.new_menu.actions()] == ['文件…', '文件分类…']
    calls = []
    monkeypatch.setattr(window.file_list, 'request_category', lambda: calls.append(True))
    window.new_category_action.trigger()
    assert calls == [True]


def test_open_folder_targets_inactive_file_without_changing_editor(window, tmp_path, monkeypatch):
    path = tmp_path / '目录 空格' / 'note.md'
    path.parent.mkdir()
    path.write_text('saved', encoding='utf-8')
    window.open_document_path(path)
    saved = window.current_session
    window.new_document('txt')
    window.editor.setPlainText('current draft')
    active = window.current_session
    urls = []
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: urls.append(url) or True)
    assert window.open_document_folder(saved)
    assert Path(urls[0].toLocalFile()) == path.parent.resolve()
    assert window.current_session is active
    assert window.editor.source_text() == 'current draft'
    assert not window.open_document_folder(active)
    assert len(urls) == 1


def test_open_folder_reports_shell_failure_in_chinese(window, tmp_path, monkeypatch):
    path = tmp_path / 'note.txt'
    path.write_text('', encoding='utf-8')
    window.open_document_path(path)
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda _: False)
    assert not window.open_document_folder()
    assert '无法打开' in window.statusBar().currentMessage()
