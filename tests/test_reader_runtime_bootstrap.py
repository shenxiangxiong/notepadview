import hashlib
from pathlib import Path

import pytest

from packaging_tools import reader_runtime
from scripts import build


def payload(root, engine):
    executable = root / ('program/soffice.exe' if engine == 'libreoffice' else 'ebook-convert.exe')
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_bytes(b'engine')
    return root


def test_prepare_uses_project_cache_without_downloading(tmp_path, monkeypatch):
    for engine in ('libreoffice', 'calibre'):
        payload(tmp_path / 'build' / 'reader-runtimes' / engine, engine)
    monkeypatch.setattr(reader_runtime, '_download_runtime', lambda *args: pytest.fail('unexpected download'), raising=False)
    env = reader_runtime.prepare_windows_runtimes(tmp_path, env={})
    assert Path(env['NOTEPADVIEW_LIBREOFFICE_DIR']).name == 'libreoffice'
    assert Path(env['NOTEPADVIEW_CALIBRE_DIR']).name == 'calibre'


def test_prepare_downloads_missing_engines_and_reuses_them(tmp_path, monkeypatch):
    downloads = []

    def download(engine, cache):
        downloads.append(engine)
        return payload(cache / engine, engine)

    monkeypatch.setattr(reader_runtime, '_download_runtime', download, raising=False)
    first = reader_runtime.prepare_windows_runtimes(tmp_path, env={})
    second = reader_runtime.prepare_windows_runtimes(tmp_path, env={})
    assert first == second
    assert downloads == ['libreoffice', 'calibre']


def test_explicit_invalid_directory_does_not_silently_download(tmp_path, monkeypatch):
    monkeypatch.setattr(reader_runtime, '_download_runtime', lambda *args: pytest.fail('unexpected download'), raising=False)
    with pytest.raises(RuntimeError, match='NOTEPADVIEW_LIBREOFFICE_DIR'):
        reader_runtime.prepare_windows_runtimes(tmp_path, env={'NOTEPADVIEW_LIBREOFFICE_DIR': str(tmp_path / 'missing')})


def test_verified_download_rejects_corrupt_cache(tmp_path, monkeypatch):
    target = tmp_path / 'engine.msi'
    target.write_bytes(b'corrupt')
    content = b'official installer'
    calls = []

    def retrieve(url, destination):
        calls.append(url)
        Path(destination).write_bytes(content)

    monkeypatch.setattr(reader_runtime, '_retrieve', retrieve, raising=False)
    digest = hashlib.sha256(content).hexdigest()
    reader_runtime._verified_download('https://example.test/engine.msi', target, digest)
    reader_runtime._verified_download('https://example.test/engine.msi', target, digest)
    assert target.read_bytes() == content
    assert len(calls) == 1


def test_official_download_failure_uses_verified_mirror(tmp_path, monkeypatch):
    attempts = []

    def retrieve(args, **kwargs):
        attempts.append(args[-1])
        if len(attempts) == 1:
            raise reader_runtime.subprocess.CalledProcessError(22, args)
        Path(args[-2]).write_bytes(b'installer')

    monkeypatch.setattr(reader_runtime.subprocess, 'run', retrieve)
    info = reader_runtime._WINDOWS_DOWNLOADS['libreoffice']
    reader_runtime._retrieve(info['url'], tmp_path / 'installer.msi')
    assert attempts[0] == info['url']
    assert attempts[1].startswith('https://mirrors.nju.edu.cn/tdf/')


def test_bad_download_is_not_cached(tmp_path, monkeypatch):
    target = tmp_path / 'engine.msi'
    monkeypatch.setattr(reader_runtime, '_retrieve', lambda url, dest: Path(dest).write_bytes(b'bad'), raising=False)
    with pytest.raises(RuntimeError, match='SHA256'):
        reader_runtime._verified_download('https://example.test/engine.msi', target, '0' * 64)
    assert not target.exists()
    assert not target.with_suffix('.msi.part').exists()


@pytest.mark.parametrize('engine', ['libreoffice', 'calibre'])
def test_download_extracts_complete_tree_and_records_origin(tmp_path, monkeypatch, engine):
    installer = tmp_path / 'installer.msi'
    installer.touch()
    monkeypatch.setattr(reader_runtime, '_verified_download', lambda *args: installer)

    def extract(args, **kwargs):
        target = Path(next(arg.split('=', 1)[1] for arg in args if arg.startswith('TARGETDIR=')))
        source = payload(target / 'vendor' / engine, engine)
        (source / 'LICENSE').write_text('vendor license', encoding='utf-8')
        (source / 'dependent.dll').write_bytes(b'dependency')
        return type('Result', (), {'returncode': 0})()

    monkeypatch.setattr(reader_runtime.subprocess, 'run', extract)
    monkeypatch.setattr(reader_runtime.shutil, 'move', lambda *args: pytest.fail('MSI directories must be copied, not moved'))
    destination = reader_runtime._download_runtime(engine, tmp_path)
    assert (destination / 'dependent.dll').read_bytes() == b'dependency'
    assert (destination / 'LICENSE').read_text(encoding='utf-8') == 'vendor license'
    assert (destination / 'runtime-origin.json').is_file()
    assert not list(tmp_path.glob(engine + '-*'))


def test_extraction_failure_keeps_previous_runtime(tmp_path, monkeypatch):
    installer = tmp_path / 'installer.msi'
    installer.touch()
    source = payload(tmp_path / 'calibre', 'calibre')
    monkeypatch.setattr(reader_runtime, '_verified_download', lambda *args: installer)
    monkeypatch.setattr(reader_runtime.subprocess, 'run', lambda *args, **kwargs: type('Result', (), {'returncode': 1603})())
    with pytest.raises(RuntimeError, match='1603'):
        reader_runtime._download_runtime('calibre', tmp_path)
    assert (source / 'ebook-convert.exe').is_file()


def test_copy_failure_keeps_previous_runtime(tmp_path, monkeypatch):
    installer = tmp_path / 'installer.msi'
    installer.touch()
    source = payload(tmp_path / 'calibre', 'calibre')
    monkeypatch.setattr(reader_runtime, '_verified_download', lambda *args: installer)

    def extract(args, **kwargs):
        target = Path(next(arg.split('=', 1)[1] for arg in args if arg.startswith('TARGETDIR=')))
        payload(target / 'vendor', 'calibre')
        return type('Result', (), {'returncode': 0})()

    def fail_copy(*args, **kwargs):
        raise OSError('copy interrupted')

    monkeypatch.setattr(reader_runtime.subprocess, 'run', extract)
    monkeypatch.setattr(reader_runtime.shutil, 'copytree', fail_copy)
    with pytest.raises(OSError, match='copy interrupted'):
        reader_runtime._download_runtime('calibre', tmp_path)
    assert (source / 'ebook-convert.exe').read_bytes() == b'engine'


def test_build_prepares_engines_before_pyinstaller(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(build, 'ROOT', tmp_path)
    monkeypatch.setattr(build, 'prepare_windows_runtimes', lambda root: events.append('prepare') or {}, raising=False)
    monkeypatch.setattr(build, 'run', lambda *args: events.append(args[0]))
    monkeypatch.setattr(build, 'assemble_setup', lambda **kwargs: events.append('setup'))
    build.build()
    assert events[0] == 'prepare'
