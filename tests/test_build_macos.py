import hashlib
import importlib
import plistlib
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest


def builder():
    try:
        return importlib.import_module('scripts.build_macos')
    except ModuleNotFoundError:
        pytest.fail('macOS builder is not implemented')


@pytest.mark.parametrize('system,machine,version,message', [
    ('Windows', 'ARM64', '13.0', 'macOS'),
    ('Darwin', 'x86_64', '13.0', 'Rosetta'),
    ('Darwin', 'arm64', '12.7', '13'),
])
def test_rejects_unsupported_host(system, machine, version, message):
    module = builder()
    with patch.object(module.platform, 'system', return_value=system), \
         patch.object(module.platform, 'machine', return_value=machine), \
         patch.object(module.platform, 'mac_ver', return_value=(version, '', '')):
        with pytest.raises(RuntimeError, match=message):
            module.validate_host()


def fake_project(root):
    (root / 'notepadview' / 'assets').mkdir(parents=True)
    (root / 'logo').mkdir()
    (root / 'logo' / 'nvlogo.png').write_bytes(b'logo')
    for name in ('README.md', 'THIRD_PARTY_NOTICES.md', 'requirements.txt'):
        (root / name).write_text(name)
    (root / 'dist').mkdir()
    (root / 'dist' / 'SHA256SUMS.txt').write_text('windows unchanged')


@pytest.mark.parametrize('failure', [None, 'hdiutil', 'missing-dmg', 'missing-app', 'codesign', 'verify', 'missing-icon', 'existing-app'])
def test_build_transaction_and_isolation(tmp_path, failure):
    module = builder()
    fake_project(tmp_path)
    if failure == 'existing-app':
        previous = tmp_path / 'dist' / 'macos' / 'Notepad View.app'
        previous.mkdir(parents=True)
        (previous / 'keep.txt').write_text('previous build')
    calls = []
    staged = []

    def stage_runtimes(destination, *, env, system):
        runtime_root = Path(destination) / 'reader-runtimes'
        runtime_root.mkdir(parents=True)
        (runtime_root / 'libreoffice').mkdir()
        (runtime_root / 'calibre').mkdir()
        staged.append((Path(destination), system))
        return runtime_root

    def run(*args, **kwargs):
        calls.append(args)
        if '-m' in args and 'PyInstaller' in args:
            output = Path(args[args.index('--distpath') + 1]) / 'Notepad View.app'
            if failure != 'missing-app':
                (output / 'Contents' / 'MacOS').mkdir(parents=True)
                (output / 'Contents' / 'MacOS' / 'Notepad View').write_bytes(b'mach-o')
                (output / 'Contents' / 'Info.plist').write_bytes(plistlib.dumps({}))
        elif args[0] == 'iconutil' and failure != 'missing-icon':
            Path(args[args.index('--output') + 1]).write_bytes(b'icns')
        elif args[0] == 'hdiutil' and args[1] == 'create':
            staging = Path(args[args.index('-srcfolder') + 1])
            assert (staging / 'Applications').is_symlink()
            assert (staging / 'Notepad View.app').is_dir()
            if failure == 'hdiutil':
                raise subprocess.CalledProcessError(1, args)
            if failure != 'missing-dmg':
                Path(args[-1]).write_bytes(b'dmg')
        elif args[:2] == ('hdiutil', 'verify') and failure == 'verify':
            raise subprocess.CalledProcessError(1, args)
        elif args[0] == 'codesign' and failure == 'codesign':
            raise subprocess.CalledProcessError(1, args)
        elif args[0] == 'codesign':
            assert staged and staged[0][0].name == 'Resources'
            assert (staged[0][0] / 'reader-runtimes' / 'libreoffice').is_dir()

    def licenses(destination):
        destination.mkdir(parents=True)
        (destination / 'LICENSE').write_text('license')

    with patch.object(module, 'validate_host'), patch.object(module, 'validate_dependencies'), \
         patch.object(module, 'run', side_effect=run), \
         patch.object(module, 'stage_reader_runtimes', side_effect=stage_runtimes, create=True), \
         patch.object(module, 'collect_licenses', side_effect=licenses), \
         patch.object(module.subprocess, 'check_output', return_value='locked dependencies'), \
         patch.object(module.os, 'symlink', side_effect=lambda target, link: Path(link).write_text(target)):
        # Windows 创建符号链接可能需要管理员权限；只模拟系统调用，仍检查 staging 的内容。
        with patch.object(Path, 'is_symlink', return_value=True):
            if failure:
                with pytest.raises((RuntimeError, subprocess.CalledProcessError)):
                    module.build(tmp_path)
                assert not list((tmp_path / 'dist' / 'macos').rglob('SHA256SUMS.txt'))
            else:
                output = module.build(tmp_path)
                dmg = output / 'NotepadView-1.0.0-macOS-arm64.dmg'
                assert dmg.read_bytes() == b'dmg'
                assert (output / 'Notepad View.app' / 'Contents' / 'Resources' / 'licenses' / 'LICENSE').is_file()
                info = plistlib.loads((output / 'Notepad View.app' / 'Contents' / 'Info.plist').read_bytes())
                assert info['LSMinimumSystemVersion'] == '13.0'
                assert info['CFBundleShortVersionString'] == '1.0.0'
                assert (output / 'SHA256SUMS.txt').read_text() == hashlib.sha256(b'dmg').hexdigest() + '  ' + dmg.name + '\n'
                command = next(args for args in calls if 'PyInstaller' in args)
                assert command[command.index('--target-arch') + 1] == 'arm64'
                assert '--osx-bundle-identifier' in command
                assert 'pyside6_scintilla' in command
                assert 'PySide6.QtPdfWidgets' in command
                assert 'PySide6.QtWebEngineWidgets' in command
                assert any(args[:2] == ('hdiutil', 'verify') for args in calls)
                assert len(staged) == 1
                assert staged[0][0].name == 'Resources'
                assert staged[0][1] == 'Darwin'
    if failure == 'existing-app':
        assert (previous / 'keep.txt').read_text() == 'previous build'
        assert not calls
    assert (tmp_path / 'dist' / 'SHA256SUMS.txt').read_text() == 'windows unchanged'
    assert not list((tmp_path / 'build' / 'macos').glob('run-*'))
