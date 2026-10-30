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


def test_settings_opens_windows_default_apps(window, monkeypatch):
    from PySide6.QtGui import QDesktopServices
    opened = []
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: opened.append(url.toString()) or True)
    action = next(action for action in window.settings_menu.actions()
                  if action.text() == '默认打开程序…')
    action.trigger()
    assert opened == ['ms-settings:defaultapps?registeredAppUser=NotepadView']


def test_default_apps_launch_failure_shows_manual_steps(window, monkeypatch):
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import QMessageBox
    messages = []
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: False)
    monkeypatch.setattr(QMessageBox, 'information', lambda parent, title, text: messages.append(text))
    window.open_default_apps()
    assert '默认应用' in messages[0]
    assert '始终使用' in messages[0]


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
