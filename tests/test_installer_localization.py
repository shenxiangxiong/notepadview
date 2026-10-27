import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest
from PySide6.QtWidgets import QApplication


ROOT = Path(__file__).resolve().parents[1]
UNINSTALL = ROOT / 'packaging_tools' / 'uninstall.ps1'


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize(('case', 'expected'), [
    ('root', '请选择应用程序的子目录'),
    ('nonempty', '所选目录不是空目录'),
    ('traversal', '安装包包含无效路径'),
    ('missing_exe', '安装包中缺少 NotepadView.exe'),
])
def test_install_validation_errors_are_chinese(tmp_path, case, expected):
    from packaging_tools.installer_core import install_payload, validate_target

    target = tmp_path / 'app'
    if case == 'root':
        with pytest.raises(ValueError, match=expected):
            validate_target(Path(tmp_path.anchor))
        return
    entries = {'NotepadView.exe': b'MZ'}
    if case == 'nonempty':
        target.mkdir()
        (target / 'personal.txt').write_text('keep')
    elif case == 'traversal':
        entries['../outside.txt'] = b'bad'
    else:
        entries = {'data.txt': b'data'}
    archive = tmp_path / 'payload.zip'
    with zipfile.ZipFile(archive, 'w') as payload:
        for name, content in entries.items():
            payload.writestr(name, content)
    with pytest.raises((ValueError, FileExistsError), match=expected):
        install_payload(archive, target)


@pytest.mark.parametrize('argv', [
    ['--unknown'], ['--dir'], ['--silent'], ['--silent=yes'], ['unexpected'],
])
def test_installer_argument_errors_are_chinese(monkeypatch, capsys, argv):
    import packaging_tools.setup as setup

    monkeypatch.setattr(sys, 'argv', ['NotepadView-Setup.exe', *argv])
    with pytest.raises(SystemExit) as error:
        setup.main()
    assert error.value.code == 2
    output = capsys.readouterr().err
    assert '用法：' in output
    assert '错误：' in output
    assert '--' in output
    for english in ('usage:', 'error:', 'unrecognized arguments:', 'expected one argument',
                    'requires --dir', 'ignored explicit argument', 'ambiguous option:'):
        assert english not in output


def test_installer_help_is_chinese(monkeypatch, capsys):
    import packaging_tools.setup as setup

    monkeypatch.setattr(sys, 'argv', ['NotepadView-Setup.exe', '--help'])
    with pytest.raises(SystemExit) as error:
        setup.main()
    assert error.value.code == 0
    output = capsys.readouterr().out
    for text in ('用法：', '选项', '显示帮助', '静默安装', '安装目录', '注册'):
        assert text in output
    assert 'show this help message' not in output
    assert 'options:' not in output


@pytest.mark.parametrize('stage', ['install', 'register'])
def test_worker_translates_system_errors(app, monkeypatch, tmp_path, stage):
    import packaging_tools.setup as setup

    def fail(*args):
        raise PermissionError('Access is denied (raw system detail)')

    monkeypatch.setattr(setup, 'install_payload', fail if stage == 'install' else lambda *args: None)
    monkeypatch.setattr(setup, 'register_install', fail)
    worker = setup.InstallWorker(tmp_path / 'app', False)
    messages = []
    worker.finished_install.connect(messages.append)
    worker.run()
    assert len(messages) == 1
    assert any('\u4e00' <= character <= '\u9fff' for character in messages[0])
    assert 'Access is denied' not in messages[0]
    assert worker.files_installed == (stage == 'register')


@pytest.mark.parametrize('no_register', [False, True])
def test_silent_install_keeps_success_quiet(monkeypatch, capsys, tmp_path, no_register):
    import packaging_tools.setup as setup

    calls = []
    monkeypatch.setattr(setup, 'install_payload', lambda *args: calls.append('install'))
    monkeypatch.setattr(setup, 'register_install', lambda *args: calls.append('register'))
    argv = ['setup', '--silent', '--dir', str(tmp_path)]
    if no_register:
        argv.append('--no-register')
    monkeypatch.setattr(sys, 'argv', argv)
    setup.main()
    assert calls == (['install'] if no_register else ['install', 'register'])
    output = capsys.readouterr()
    assert output.out == output.err == ''


@pytest.mark.parametrize('stage', ['install', 'register'])
def test_silent_install_failure_is_chinese_without_traceback(monkeypatch, capsys, tmp_path, stage):
    import packaging_tools.setup as setup

    def fail(*args):
        raise OSError('raw English system failure')

    monkeypatch.setattr(setup, 'install_payload', fail if stage == 'install' else lambda *args: None)
    monkeypatch.setattr(setup, 'register_install', fail)
    monkeypatch.setattr(sys, 'argv', ['setup', '--silent', '--dir', str(tmp_path)])
    with pytest.raises(SystemExit) as error:
        setup.main()
    assert error.value.code == 1
    output = capsys.readouterr()
    assert output.out == ''
    assert '安装失败' in output.err
    assert 'raw English' not in output.err
    assert 'Traceback' not in output.err


def test_installed_app_launch_failure_is_chinese(app, monkeypatch):
    import packaging_tools.setup as setup

    def fail(*args, **kwargs):
        raise FileNotFoundError('The system cannot find the file specified')

    monkeypatch.setattr(setup.subprocess, 'Popen', fail)
    window = setup.SetupWindow()
    window.complete = True
    try:
        window.install()
        assert '启动失败' in window.status.text()
        assert 'The system cannot find' not in window.status.text()
        assert window.complete
    finally:
        window.close()


def test_uninstaller_source_uses_bom_and_chinese_dialogs():
    data = UNINSTALL.read_bytes()
    assert data.startswith(b'\xef\xbb\xbf'), 'Windows PowerShell requires a BOM for Chinese source'
    source = data.decode('utf-8-sig')
    for text in ('卸载', '取消', '确定', '请先关闭 Notepad View', '您的文档将会保留', '卸载完成'):
        assert text in source
    for english in ('Installation manifest is missing', 'Invalid installation manifest',
                    'Manifest contains', 'Close Notepad View', 'Uninstall Notepad View?',
                    'was uninstalled'):
        assert english not in source


@pytest.mark.parametrize(('manifest', 'expected'), [
    (None, '找不到安装清单'),
    ('{broken', '安装清单'),
    (json.dumps({'app': 'Other', 'files': []}), '安装清单无效'),
    (json.dumps({'app': 'NotepadView', 'files': ['../personal.txt']}), '安装目录之外'),
])
def test_silent_uninstall_errors_are_chinese_and_preserve_files(tmp_path, manifest, expected):
    target = tmp_path / 'application'
    target.mkdir()
    script = target / 'uninstall.ps1'
    script.write_bytes(UNINSTALL.read_bytes())
    personal = tmp_path / 'personal.txt'
    personal.write_text('keep')
    if manifest is not None:
        (target / 'notepadview-install.json').write_text(manifest, encoding='utf-8')
    completed = subprocess.run([
        'powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', str(script), '-Silent', '-NoRegister',
    ], capture_output=True, timeout=20)
    assert completed.returncode == 1
    output = completed.stderr.decode('utf-8')
    assert expected in output
    for english in ('Exception', 'CategoryInfo', 'FullyQualifiedErrorId', 'At ', 'broken'):
        assert english not in output
    assert completed.stdout == b''
    assert personal.read_text() == 'keep'
    assert script.exists()


def test_registration_subprocess_does_not_leak_english_errors(monkeypatch, capfd, tmp_path):
    from contextlib import nullcontext
    import winreg
    from packaging_tools.installer_core import register_install

    run = subprocess.run
    monkeypatch.setattr(winreg, 'CreateKey', lambda *args: nullcontext(None))
    monkeypatch.setattr(winreg, 'SetValueEx', lambda *args: None)

    def fail_registration(command, **kwargs):
        return run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                    "[Console]::Error.WriteLine('raw English registration failure'); exit 1"], **kwargs)

    monkeypatch.setattr(subprocess, 'run', fail_registration)
    with pytest.raises(subprocess.CalledProcessError) as error:
        register_install(tmp_path)
    assert b'raw English registration failure' in (error.value.stderr or b'')
    output = capfd.readouterr()
    assert output.out == output.err == ''


def prepare_uninstall(tmp_path):
    target = tmp_path / 'application'
    target.mkdir()
    (target / 'uninstall.ps1').write_bytes(UNINSTALL.read_bytes())
    (target / 'NotepadView.exe').write_bytes(b'MZ-test')
    (target / 'notepadview-install.json').write_text(json.dumps({
        'app': 'NotepadView', 'files': ['NotepadView.exe', 'uninstall.ps1'],
    }), encoding='utf-8')
    return target


def test_silent_uninstall_locked_file_error_is_chinese(tmp_path):
    target = prepare_uninstall(tmp_path)
    with (target / 'NotepadView.exe').open('rb'):
        completed = subprocess.run([
            'powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
            '-File', str(target / 'uninstall.ps1'), '-Silent', '-NoRegister',
        ], capture_output=True, timeout=20)
    assert completed.returncode == 1
    output = completed.stderr.decode('utf-8')
    assert '卸载失败' in output
    assert '权限' in output or '占用' in output
    assert 'Exception' not in output
    assert 'CategoryInfo' not in output
    assert 'cannot access' not in output
    assert (target / 'NotepadView.exe').exists()
    assert (target / 'notepadview-install.json').exists()


@pytest.mark.parametrize('mode', ['running', 'cancel', 'confirm'])
def test_uninstall_process_check_and_confirmation_keep_behavior(tmp_path, mode):
    target = prepare_uninstall(tmp_path)
    environment = dict(os.environ, NOTEPADVIEW_TEST_INSTALL_ROOT=str(target))
    if mode == 'running':
        command = '''function Get-Process {
            [PSCustomObject]@{Path = (Join-Path $env:NOTEPADVIEW_TEST_INSTALL_ROOT 'NotepadView.exe')}
        }
        & (Join-Path $env:NOTEPADVIEW_TEST_INSTALL_ROOT 'uninstall.ps1') -Silent -NoRegister
        exit $LASTEXITCODE
        '''
    else:
        environment['NOTEPADVIEW_TEST_ANSWER'] = 'Cancel' if mode == 'cancel' else 'OK'
        command = '''Add-Type -AssemblyName System.Windows.Forms
        function Get-TestAnswer { return [Windows.Forms.DialogResult]::$env:NOTEPADVIEW_TEST_ANSWER }
        Set-Alias -Name Show-UninstallDialog -Value Get-TestAnswer
        & (Join-Path $env:NOTEPADVIEW_TEST_INSTALL_ROOT 'uninstall.ps1') -NoRegister
        exit $LASTEXITCODE
        '''
    completed = subprocess.run([
        'powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-Command', command,
    ], env=environment, capture_output=True, timeout=20)
    assert completed.returncode == (1 if mode == 'running' else 0), completed.stderr.decode('utf-8')
    assert completed.stdout == b''
    if mode == 'running':
        assert '请先关闭 Notepad View' in completed.stderr.decode('utf-8')
    else:
        assert completed.stderr == b''
    assert (target / 'NotepadView.exe').exists() == (mode != 'confirm')
    assert (target / 'notepadview-install.json').exists() == (mode != 'confirm')
