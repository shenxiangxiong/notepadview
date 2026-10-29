from test_ui import app, window


def test_help_menu_opens_readme_without_changing_document(window):
    session = window.current_session
    mode = window.mode
    window.help_action.trigger()
    dialog = window.help_dialog
    assert dialog.isVisible()
    assert dialog.browser.isReadOnly()
    text = dialog.browser.toPlainText()
    assert 'Notepad View' in text
    assert '默认视图' in text
    assert '常用快捷键' in text
    assert window.current_session is session
    assert window.mode == mode
    window.help_action.trigger()
    assert window.help_dialog is dialog
    dialog.close()


def test_help_finds_packaged_readme(tmp_path):
    from notepadview.help import find_readme
    executable = tmp_path / 'NotepadView.exe'
    readme = tmp_path / 'README.md'
    readme.write_text('# Packaged help', encoding='utf-8')
    assert find_readme(executable=executable, frozen=True) == readme
    resources = tmp_path / 'Resources'
    resources.mkdir()
    mac_executable = tmp_path / 'MacOS' / 'NotepadView'
    (resources / 'README.md').write_text('# Mac help', encoding='utf-8')
    assert find_readme(executable=mac_executable, frozen=True) == resources / 'README.md'


def test_help_missing_readme_shows_message(app, tmp_path):
    from notepadview.help import HelpDialog
    dialog = HelpDialog(readme=tmp_path / 'missing.md')
    assert '帮助文档暂时无法读取' in dialog.browser.toPlainText()
    dialog.close()
