import json
import os
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from packaging_tools import installer_core as core


def archive(tmp_path, name, files):
    path = tmp_path / name
    with zipfile.ZipFile(path, 'w') as output:
        for filename, content in files.items():
            output.writestr(filename, content)
    return path


def old_install(tmp_path):
    target = tmp_path / 'custom-location'
    core.install_payload(archive(tmp_path, 'old.zip', {
        'NotepadView.exe': b'MZ-old', '_internal/obsolete.dll': b'old',
        '_internal/shared.dll': b'old-shared',
    }), target)
    return target


def new_payload(tmp_path):
    return archive(tmp_path, 'new.zip', {
        'NotepadView.exe': b'MZ-new', '_internal/shared.dll': b'new-shared',
        '_internal/new.dll': b'new',
    })


def snapshot(target):
    return {str(path.relative_to(target)): path.read_bytes()
            for path in target.rglob('*') if path.is_file()}


def test_upgrade_replaces_owned_files_removes_obsolete_and_preserves_user_data(tmp_path):
    target = old_install(tmp_path)
    for name in ('personal.txt', 'settings.ini', 'settings.ini.workspace.json', '_internal/user.txt'):
        (target / name).write_text('user data', encoding='utf-8')
    core.install_payload(new_payload(tmp_path), target)
    assert (target / 'NotepadView.exe').read_bytes() == b'MZ-new'
    assert (target / '_internal/shared.dll').read_bytes() == b'new-shared'
    assert not (target / '_internal/obsolete.dll').exists()
    for name in ('personal.txt', 'settings.ini', 'settings.ini.workspace.json', '_internal/user.txt'):
        assert (target / name).read_text() == 'user data'
    manifest = json.loads((target / 'notepadview-install.json').read_text())
    assert set(manifest['files']) == {'NotepadView.exe', '_internal/shared.dll', '_internal/new.dll'}
    assert set(tmp_path.iterdir()) == {target, tmp_path / 'old.zip', tmp_path / 'new.zip'}


@pytest.mark.parametrize('failure_step', ['backup', 'publish'])
def test_upgrade_replace_failure_restores_entire_old_directory(tmp_path, monkeypatch, failure_step):
    target = old_install(tmp_path)
    (target / 'personal.txt').write_text('keep')
    before = snapshot(target)
    real_replace = os.replace
    def fail_once(source, destination):
        if ((failure_step == 'backup' and Path(source) == target) or
                (failure_step == 'publish' and Path(destination) == target
                 and Path(source).name.startswith('.notepadview-install-'))):
            raise PermissionError('file in use')
        return real_replace(source, destination)
    monkeypatch.setattr(core.os, 'replace', fail_once)
    with pytest.raises(OSError, match='关闭'):
        core.install_payload(new_payload(tmp_path), target)
    assert snapshot(target) == before
    assert not list(tmp_path.glob('.notepadview-*'))


@pytest.mark.skipif(os.name != 'nt', reason='Windows 文件共享锁')
def test_upgrade_locked_executable_keeps_old_install(tmp_path):
    import ctypes
    from ctypes import wintypes
    target = old_install(tmp_path)
    before = snapshot(target)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(target / 'NotepadView.exe'), 0x80000000, 1, None, 3, 0x80, None)
    assert handle != wintypes.HANDLE(-1).value
    try:
        with pytest.raises(OSError, match='关闭'):
            core.install_payload(new_payload(tmp_path), target)
        assert snapshot(target) == before
    finally:
        kernel.CloseHandle(handle)
    assert not list(tmp_path.glob('.notepadview-*'))


def test_upgrade_extraction_failure_does_not_touch_old_install(tmp_path):
    target = old_install(tmp_path)
    before = snapshot(target)
    def fail_progress(value):
        raise RuntimeError('extract failed')
    with pytest.raises(RuntimeError, match='extract failed'):
        core.install_payload(new_payload(tmp_path), target, fail_progress)
    assert snapshot(target) == before
    assert not list(tmp_path.glob('.notepadview-*'))


@pytest.mark.parametrize('manifest,executable', [
    ({'app': 'Other', 'files': ['NotepadView.exe']}, b'MZ-other'),
    ({'app': 'NotepadView', 'files': ['../personal.txt', 'NotepadView.exe']}, b'MZ-old'),
    ({'app': 'NotepadView', 'files': ['NotepadView.exe']}, b'not an exe'),
    ({'app': 'NotepadView', 'files': []}, b'MZ-old'),
    ({'app': 'NotepadView', 'files': ['NotepadView.exe']}, None),
])
def test_unrelated_or_invalid_install_is_not_overwritten(tmp_path, manifest, executable):
    target = tmp_path / 'unrelated'
    target.mkdir()
    (target / 'notepadview-install.json').write_text(json.dumps(manifest))
    if executable:
        (target / 'NotepadView.exe').write_bytes(executable)
    (target / 'personal.txt').write_text('keep')
    before = snapshot(target)
    with pytest.raises(FileExistsError):
        core.install_payload(new_payload(tmp_path), target)
    assert snapshot(target) == before


def test_upgrade_does_not_overwrite_unregistered_user_file(tmp_path):
    target = old_install(tmp_path)
    (target / '_internal/new.dll').write_bytes(b'user file')
    before = snapshot(target)
    with pytest.raises(FileExistsError):
        core.install_payload(new_payload(tmp_path), target)
    assert snapshot(target) == before


def registry(location=None, error=None):
    class Key:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
    def open_key(hive, path):
        assert hive == 'HKCU'
        assert path == r'Software\Microsoft\Windows\CurrentVersion\Uninstall\NotepadView'
        if error:
            raise error
        return Key()
    return SimpleNamespace(HKEY_CURRENT_USER='HKCU', REG_SZ=1, OpenKey=open_key,
                           QueryValueEx=lambda key, name: (location, 1))


def test_default_target_prefers_valid_registered_install(tmp_path, monkeypatch):
    target = old_install(tmp_path)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    assert core.default_install_target(registry(str(target))) == target


@pytest.mark.parametrize('kind', ['missing-key', 'missing-directory', 'unrelated', 'empty-value'])
def test_default_target_first_install_or_invalid_registration(tmp_path, monkeypatch, kind):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    unrelated = tmp_path / 'unrelated'
    unrelated.mkdir()
    (unrelated / 'personal.txt').write_text('keep')
    values = {'missing-key': registry(error=FileNotFoundError()),
              'missing-directory': registry(str(tmp_path / 'missing')),
              'unrelated': registry(str(unrelated)), 'empty-value': registry('')}
    assert core.default_install_target(values[kind]) == tmp_path / 'local' / 'Programs' / 'NotepadView'


def test_locked_install_error_remains_actionable_in_ui(tmp_path, monkeypatch):
    from notepadview.errors import chinese_error
    target = old_install(tmp_path)
    def locked(*args):
        raise PermissionError('file in use')
    monkeypatch.setattr(core, '_check_replaceable', locked)
    with pytest.raises(PermissionError) as caught:
        core.install_payload(new_payload(tmp_path), target)
    assert '关闭' in chinese_error(caught.value)


def test_setup_window_uses_discovered_default(monkeypatch):
    from PySide6.QtWidgets import QApplication
    import packaging_tools.setup as setup
    application = QApplication.instance() or QApplication([])
    expected = Path(r'D:\Custom\NotepadView')
    monkeypatch.setattr(setup, 'default_install_target', lambda: expected)
    window = setup.SetupWindow()
    try:
        assert window.location.text() == str(expected)
    finally:
        window.close()
