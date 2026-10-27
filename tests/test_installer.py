import json
from pathlib import Path
import zipfile
import pytest


def payload(tmp_path, name='payload.zip', entries=None):
    path = tmp_path / name
    with zipfile.ZipFile(path, 'w') as archive:
        for name, content in (entries or {'NotepadView.exe': b'MZ-test', '_internal/data.txt': b'content'}).items():
            archive.writestr(name, content)
    return path


def test_install_extracts_and_records_manifest(tmp_path):
    from packaging_tools.installer_core import install_payload
    target = tmp_path / 'application'
    install_payload(payload(tmp_path), target)
    assert (target / 'NotepadView.exe').read_bytes() == b'MZ-test'
    manifest = json.loads((target / 'notepadview-install.json').read_text())
    assert manifest['app'] == 'NotepadView'
    assert '_internal/data.txt' in manifest['files']


def test_install_retries_temporary_directory_lock(tmp_path, monkeypatch):
    import packaging_tools.installer_core as core
    target = tmp_path / 'application'
    original = core.os.replace
    attempts = []

    def temporarily_locked(source, destination):
        attempts.append(destination)
        if len(attempts) < 3:
            raise PermissionError('temporary directory lock')
        return original(source, destination)

    monkeypatch.setattr(core.os, 'replace', temporarily_locked)
    install_archive = payload(tmp_path)
    core.install_payload(install_archive, target)
    assert (target / 'NotepadView.exe').read_bytes() == b'MZ-test'
    assert len(attempts) == 3


def test_install_lzma_payload_preserves_every_file(tmp_path):
    from packaging_tools.installer_core import install_payload
    entries = {'NotepadView.exe': b'MZ-test',
               'reader-runtimes/中文目录/runtime.bin': bytes(range(256)) * 128,
               '_internal/empty.txt': b''}
    archive_path = tmp_path / 'compressed.zip'
    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_LZMA) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    target = tmp_path / 'application'
    install_payload(archive_path, target)
    for name, content in entries.items():
        assert (target / name).read_bytes() == content
    manifest = json.loads((target / 'notepadview-install.json').read_text())
    assert set(manifest['files']) == set(entries)


def test_solid_payload_preserves_all_files_and_upgrade(tmp_path):
    from packaging_tools.payload import write_payload
    from packaging_tools.installer_core import install_payload
    source = tmp_path / 'source'
    source.mkdir()
    entries = {'NotepadView.exe': b'MZ-test', 'reader-runtimes/中文/runtime.bin': bytes(range(256)) * 128,
               '_internal/empty.txt': b''}
    for name, content in entries.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    archive = tmp_path / 'payload.tar.xz'
    write_payload(source, archive)
    target = tmp_path / 'app'
    install_payload(archive, target)
    (target / 'personal.txt').write_text('keep')
    install_payload(archive, target)
    assert (target / 'personal.txt').read_text() == 'keep'
    for name, content in entries.items():
        assert (target / name).read_bytes() == content
    manifest = json.loads((target / 'notepadview-install.json').read_text())
    assert set(manifest['files']) == set(entries)


@pytest.mark.parametrize('name,kind', [('../outside', 'file'), ('link', 'symlink')])
def test_solid_payload_rejects_unsafe_entries(tmp_path, name, kind):
    import io
    import tarfile
    from packaging_tools.installer_core import install_payload
    archive = tmp_path / 'payload.tar.xz'
    with tarfile.open(archive, 'w:xz') as output:
        member = tarfile.TarInfo('NotepadView.exe')
        member.size = 2
        output.addfile(member, io.BytesIO(b'MZ'))
        member = tarfile.TarInfo(name)
        if kind == 'symlink':
            member.type = tarfile.SYMTYPE
            member.linkname = '../outside'
        output.addfile(member)
    with pytest.raises(ValueError):
        install_payload(archive, tmp_path / 'app')
    assert not (tmp_path / 'outside').exists()
    assert not (tmp_path / 'app').exists()


def test_installer_rejects_path_traversal(tmp_path):
    from packaging_tools.installer_core import install_payload
    archive = payload(tmp_path, entries={'../outside.txt': b'bad', 'NotepadView.exe': b'MZ'})
    with pytest.raises(ValueError):
        install_payload(archive, tmp_path / 'app')
    assert not (tmp_path / 'outside.txt').exists()


def test_installer_preserves_nonempty_directory(tmp_path):
    from packaging_tools.installer_core import install_payload
    target = tmp_path / 'app'
    target.mkdir()
    (target / 'personal.txt').write_text('keep')
    with pytest.raises(FileExistsError):
        install_payload(payload(tmp_path), target)
    assert (target / 'personal.txt').read_text() == 'keep'


def test_installer_failure_does_not_leave_partial_target(tmp_path):
    from packaging_tools.installer_core import install_payload
    archive = payload(tmp_path, entries={'_internal/data.txt': b'data'})
    target = tmp_path / 'app'
    with pytest.raises(ValueError):
        install_payload(archive, target)
    assert not target.exists()


def test_root_subdirectory_is_valid_install_target():
    from packaging_tools.installer_core import validate_target
    assert validate_target(Path('D:/NotepadView')) == Path('D:/NotepadView')
    with pytest.raises(ValueError):
        validate_target(Path('D:/'))


def test_retry_registration_skips_existing_payload(tmp_path, monkeypatch):
    import packaging_tools.setup as setup
    calls = []
    monkeypatch.setattr(setup, 'install_payload', lambda *a: calls.append('extract'))
    def register(*args):
        calls.append('register')
        if calls.count('register') == 1:
            raise OSError('temporary registration error')
    monkeypatch.setattr(setup, 'register_install', register)
    first = setup.InstallWorker(tmp_path / 'app', False)
    first.run()
    retry = setup.InstallWorker(tmp_path / 'app', False, already_installed=first.files_installed)
    retry.run()
    assert calls == ['extract', 'register', 'register']


def test_uninstall_removes_only_installed_files(tmp_path):
    import subprocess
    from packaging_tools.installer_core import install_payload
    script = Path(__file__).parents[1] / 'packaging_tools/uninstall.ps1'
    archive = payload(tmp_path, entries={'NotepadView.exe': b'MZ-test',
                      '_internal/data.txt': b'data', 'uninstall.ps1': script.read_bytes()})
    target = tmp_path / 'application'
    install_payload(archive, target)
    personal = target / 'personal.md'
    personal.write_text('# Keep this document', encoding='utf-8')
    completed = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
                                '-ExecutionPolicy', 'Bypass', '-File', str(target / 'uninstall.ps1'),
                                '-Silent', '-NoRegister'], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert personal.read_text() == '# Keep this document'
    assert list(target.iterdir()) == [personal]


def test_installed_product_name_is_used_for_install_folder_and_shortcut():
    from packaging_tools.installer_core import APP_NAME, EXECUTABLE_NAME, INSTALL_DIR_NAME
    from packaging_tools.setup import PRODUCT_NAME
    assert PRODUCT_NAME == 'Notepad View'
    assert APP_NAME == PRODUCT_NAME
    assert INSTALL_DIR_NAME == 'NotepadView'
    assert EXECUTABLE_NAME == 'NotepadView.exe'
