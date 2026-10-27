from pathlib import Path
import zipfile
from types import SimpleNamespace

import pytest

from packaging_tools.reader_runtime import stage_reader_runtimes
from scripts import build


def make_payloads(root, system):
    libreoffice = root / 'libreoffice-source'
    calibre = root / 'calibre-source'
    if system == 'Windows':
        office_executable = libreoffice / 'program' / 'soffice.exe'
        calibre_executable = calibre / 'ebook-convert.exe'
    else:
        office_executable = libreoffice / 'LibreOffice.app' / 'Contents' / 'MacOS' / 'soffice'
        calibre_executable = calibre / 'calibre.app' / 'Contents' / 'MacOS' / 'ebook-convert'
    office_executable.parent.mkdir(parents=True)
    calibre_executable.parent.mkdir(parents=True)
    office_executable.write_bytes(b'office executable')
    calibre_executable.write_bytes(b'calibre executable')
    (libreoffice / 'LICENSE').write_text('LibreOffice license', encoding='utf-8')
    (calibre / 'LICENSE').write_text('Calibre license', encoding='utf-8')
    return libreoffice, calibre


@pytest.mark.parametrize('system', ['Windows', 'Darwin'])
def test_stages_both_platform_reader_runtimes_with_licenses(tmp_path, system):
    libreoffice, calibre = make_payloads(tmp_path, system)
    destination = tmp_path / 'distribution'
    destination.mkdir()
    env = {
        'NOTEPADVIEW_LIBREOFFICE_DIR': str(libreoffice),
        'NOTEPADVIEW_CALIBRE_DIR': str(calibre),
    }

    runtimes = stage_reader_runtimes(destination, env=env, system=system)

    assert runtimes == destination / 'reader-runtimes'
    assert (runtimes / 'libreoffice' / 'LICENSE').is_file()
    assert (runtimes / 'calibre' / 'LICENSE').is_file()
    if system == 'Windows':
        assert (runtimes / 'libreoffice' / 'program' / 'soffice.exe').read_bytes() == b'office executable'
        assert (runtimes / 'calibre' / 'ebook-convert.exe').read_bytes() == b'calibre executable'
    else:
        assert (runtimes / 'libreoffice' / 'LibreOffice.app' / 'Contents' /
                'MacOS' / 'soffice').is_file()
        assert (runtimes / 'calibre' / 'calibre.app' / 'Contents' /
                'MacOS' / 'ebook-convert').is_file()


def test_repeated_staging_handles_readonly_runtime_files(tmp_path):
    import stat
    libreoffice, calibre = make_payloads(tmp_path, 'Windows')
    library = calibre / 'legacy.dll'
    library.write_bytes(b'original library')
    library.chmod(stat.S_IREAD)
    env = {'NOTEPADVIEW_LIBREOFFICE_DIR': str(libreoffice),
           'NOTEPADVIEW_CALIBRE_DIR': str(calibre)}
    destination = tmp_path / 'distribution'
    try:
        stage_reader_runtimes(destination, env=env, system='Windows')
        stage_reader_runtimes(destination, env=env, system='Windows')
        assert (destination / 'reader-runtimes/calibre/legacy.dll').read_bytes() == b'original library'
    finally:
        library.chmod(stat.S_IWRITE | stat.S_IREAD)
        staged = destination / 'reader-runtimes/calibre/legacy.dll'
        if staged.exists():
            staged.chmod(stat.S_IWRITE | stat.S_IREAD)


def test_runtime_staging_fails_explicitly_when_release_payload_is_missing(tmp_path):
    with pytest.raises(RuntimeError, match='NOTEPADVIEW_LIBREOFFICE_DIR'):
        stage_reader_runtimes(tmp_path / 'distribution', env={}, system='Windows')


def test_runtime_staging_rejects_incomplete_payload(tmp_path):
    calibre = tmp_path / 'calibre'
    calibre.mkdir()
    office = tmp_path / 'office'
    office.mkdir()
    with pytest.raises(RuntimeError, match='soffice.exe'):
        stage_reader_runtimes(
            tmp_path / 'distribution',
            env={
                'NOTEPADVIEW_LIBREOFFICE_DIR': str(office),
                'NOTEPADVIEW_CALIBRE_DIR': str(calibre),
            },
            system='Windows',
        )


def test_windows_setup_payload_contains_staged_reader_runtimes(tmp_path, monkeypatch):
    libreoffice, calibre = make_payloads(tmp_path, 'Windows')
    root = tmp_path / 'project'
    distribution = root / 'dist' / 'NotepadView'
    distribution.mkdir(parents=True)
    (root / 'packaging_tools').mkdir()
    (root / 'packaging_tools' / 'uninstall.ps1').write_text('uninstall', encoding='utf-8')
    (root / 'README.md').write_text('readme', encoding='utf-8')
    (root / 'THIRD_PARTY_NOTICES.md').write_text('notices', encoding='utf-8')
    (root / 'notepadview' / 'assets').mkdir(parents=True)
    (root / 'base-python').mkdir()
    (root / 'base-python' / 'LICENSE.txt').write_text('python', encoding='utf-8')
    monkeypatch.setattr(build, 'ROOT', root)
    monkeypatch.setattr(build.sys, 'base_prefix', str(root / 'base-python'))
    monkeypatch.setattr(
        build.importlib.metadata, 'distribution',
        lambda name: SimpleNamespace(files=()),
    )
    monkeypatch.setattr(build.subprocess, 'check_output', lambda *args, **kwargs: 'locked\n')
    monkeypatch.setenv('NOTEPADVIEW_LIBREOFFICE_DIR', str(libreoffice))
    monkeypatch.setenv('NOTEPADVIEW_CALIBRE_DIR', str(calibre))

    def fake_run(*args):
        if args[:2] == ('-m', 'PyInstaller') and '--onefile' in args:
            (root / 'dist' / 'NotepadView-1.0.0-Setup.exe').write_bytes(b'setup')

    monkeypatch.setattr(build, 'run', fake_run)

    build.assemble_setup()

    archive_path = root / 'dist' / 'NotepadView-1.0.0-Windows-x64-portable.zip'
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
    assert any(name.endswith('reader-runtimes/libreoffice/program/soffice.exe')
               for name in names)
    assert any(name.endswith('reader-runtimes/calibre/ebook-convert.exe')
               for name in names)
    from packaging_tools.payload import open_payload
    with open_payload(root / 'build' / 'payload.tar.xz') as installer_archive:
        with zipfile.ZipFile(archive_path) as portable_archive:
            entries = installer_archive.infolist()
            assert [entry.filename for entry in entries] == portable_archive.namelist()
            for entry in entries:
                assert portable_archive.getinfo(entry.filename).compress_type == zipfile.ZIP_DEFLATED
                with installer_archive.open(entry) as source:
                    assert source.read() == portable_archive.read(entry.filename)
