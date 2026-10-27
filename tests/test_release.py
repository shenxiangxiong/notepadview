"""Build-contract checks; artifact checks run after a release has been generated."""
from pathlib import Path
import os
import struct
import zipfile

import pytest

from scripts import build


def test_build_collects_native_editor_and_sql_dependencies(monkeypatch):
    commands = []
    monkeypatch.setattr(build, 'run', lambda *args: commands.append(args))
    monkeypatch.setattr(build, 'assemble_setup', lambda: None)
    build.build()
    command = next(c for c in commands if c[:2] == ('-m', 'PyInstaller'))
    assert '--collect-all' in command
    assert command[command.index('--collect-all') + 1] == 'pyside6_scintilla'
    assert 'sqlparse' in command
    assert 'pygments.lexers' in command


@pytest.mark.skipif(os.environ.get('NOTEPAD_VERIFY_RELEASE') != '1', reason='requires freshly built artifacts')
def test_release_payload_contains_runtime_and_licenses():
    root = Path(__file__).parents[1]
    archive = root / 'dist/NotepadView-1.0.0-Windows-x64-portable.zip'
    with zipfile.ZipFile(archive) as payload:
        names = payload.namelist()
        assert payload.testzip() is None
        assert 'NotepadView.exe' in names
        assert '_internal/notepadview/assets/qtbase_zh_CN.qm' in names
        assert '_internal/notepadview/assets/dropdown-light.png' in names
        assert '_internal/notepadview/assets/dropdown-dark.png' in names
        assert '_internal/notepadview/assets/tab-close-light.png' in names
        assert '_internal/notepadview/assets/tab-close-dark.png' in names
        for suffix in ('pyside6_scintilla/_pyside6_scintilla.pyd',
                       'pyside6_scintilla/scintilla_qt.dll',
                       'pyside6_scintilla/Qt6Core5Compat.dll'):
            assert any(name.endswith(suffix) for name in names), suffix
        for component in ('pyside6-scintilla', 'sqlparse', 'regex'):
            assert any('/licenses/' + component + '/' in name for name in names)
        lock = payload.read('dependencies-lock.txt').decode('utf-8').lower()
        assert 'pyside6-scintilla==5.6.3.7' in lock
        assert 'pyside6==6.11.1' in lock
        assert 'sqlparse==' in lock
        assert 'regex==2026.9.29' in lock
        assert any(name.endswith('regex/_regex.cp312-win_amd64.pyd') for name in names)
    assert (root / 'dist/NotepadView-1.0.0-Setup.exe').stat().st_size > 1_000_000


@pytest.mark.skipif(os.environ.get('NOTEPAD_VERIFY_RELEASE') != '1', reason='requires freshly built artifacts')
def test_both_executables_embed_all_brand_icon_frames():
    import pefile

    root = Path(__file__).parents[1]
    ico = (root / 'notepadview/assets/app.ico').read_bytes()
    count = struct.unpack_from('<H', ico, 4)[0]
    frames = []
    for index in range(count):
        length, offset = struct.unpack_from('<II', ico, 6 + index * 16 + 8)
        frames.append(ico[offset:offset + length])
    assert count == 8
    for relative in ('dist/NotepadView/NotepadView.exe', 'dist/NotepadView-1.0.0-Setup.exe'):
        with pefile.PE(str(root / relative), fast_load=True) as executable:
            executable.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_RESOURCE']])
            icons = next(entry for entry in executable.DIRECTORY_ENTRY_RESOURCE.entries if entry.id == 3)
            embedded = [executable.get_data(language.data.struct.OffsetToData, language.data.struct.Size)
                        for icon in icons.directory.entries for language in icon.directory.entries]
            assert sorted(embedded) == sorted(frames), relative
    with zipfile.ZipFile(root / 'dist/NotepadView-1.0.0-Windows-x64-portable.zip') as payload:
        for name in ('app.png', 'app.ico'):
            assert payload.read('_internal/notepadview/assets/' + name) == (root / 'notepadview/assets' / name).read_bytes()


@pytest.mark.skipif(os.environ.get('NOTEPAD_VERIFY_RELEASE') != '1', reason='requires freshly built artifacts')
def test_installer_bundles_file_association_registration():
    from PyInstaller.archive.readers import CArchiveReader

    root = Path(__file__).parents[1]
    installer = CArchiveReader(str(root / 'dist/NotepadView-1.0.0-Setup.exe'))
    modules = installer.open_embedded_archive('PYZ.pyz').toc
    assert 'packaging_tools.installer_core' in modules
    assert 'packaging_tools.file_associations' in modules
