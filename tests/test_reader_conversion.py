from pathlib import Path
import subprocess

import pytest

from notepadview.reader_conversion import ReaderConversionError, ReaderConversionService
from notepadview.reader_runtime import reader_runtime_root, resolve_reader_runtime


def make_runtime(tmp_path):
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    executable = runtime / 'reader.exe'
    executable.write_bytes(b'fake executable')
    return runtime, executable


def command_factory(executable, source, output_dir):
    return [str(executable), str(source), str(output_dir)]


def test_conversion_caches_validated_output_and_invalidates_when_source_changes(tmp_path):
    runtime, executable = make_runtime(tmp_path)
    source = tmp_path / 'input.doc'
    source.write_bytes(b'first version')
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        output_dir = Path(args[-1])
        (output_dir / 'converted.pdf').write_bytes(b'%PDF-fake')
        return subprocess.CompletedProcess(args, 0, '', '')

    service = ReaderConversionService(tmp_path / 'cache', runtime, runner=runner)
    first = service.convert(source, 'reader.exe', '.pdf', command_factory)
    second = service.convert(source, 'reader.exe', '.pdf', command_factory)
    assert first == second
    assert first.read_bytes() == b'%PDF-fake'
    assert len(calls) == 1

    source.write_bytes(b'second version')
    third = service.convert(source, 'reader.exe', '.pdf', command_factory)
    assert third != first
    assert len(calls) == 2


def test_conversion_rejects_missing_engine(tmp_path):
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    source = tmp_path / 'input.doc'
    source.write_bytes(b'input')
    service = ReaderConversionService(tmp_path / 'cache', runtime)

    with pytest.raises(ReaderConversionError, match='reader.exe'):
        service.convert(source, 'reader.exe', '.pdf', command_factory)


@pytest.mark.parametrize('failure', ['exit', 'timeout'])
def test_conversion_reports_process_failure(tmp_path, failure):
    runtime, _ = make_runtime(tmp_path)
    source = tmp_path / 'input.doc'
    source.write_bytes(b'input')

    def runner(args, **kwargs):
        if failure == 'exit':
            raise subprocess.CalledProcessError(7, args, stderr='bad input')
        raise subprocess.TimeoutExpired(args, kwargs['timeout'])

    service = ReaderConversionService(tmp_path / 'cache', runtime, runner=runner)
    expected = '退出码 7' if failure == 'exit' else '超时'
    with pytest.raises(ReaderConversionError, match=expected):
        service.convert(source, 'reader.exe', '.pdf', command_factory)


@pytest.mark.parametrize('output', [None, b'', b'x' * 33])
def test_conversion_rejects_missing_empty_or_oversized_output(tmp_path, output):
    runtime, _ = make_runtime(tmp_path)
    source = tmp_path / 'input.doc'
    source.write_bytes(b'input')

    def runner(args, **kwargs):
        if output is not None:
            (Path(args[-1]) / 'converted.pdf').write_bytes(output)
        return subprocess.CompletedProcess(args, 0, '', '')

    service = ReaderConversionService(tmp_path / 'cache', runtime, runner=runner,
                                      max_output_bytes=32)
    expected = '未生成' if output is None else '空' if output == b'' else '超过'
    with pytest.raises(ReaderConversionError, match=expected):
        service.convert(source, 'reader.exe', '.pdf', command_factory)


def test_runtime_resolver_rejects_paths_outside_runtime_root(tmp_path):
    runtime, executable = make_runtime(tmp_path)
    assert resolve_reader_runtime(runtime, 'reader.exe') == executable.resolve()

    with pytest.raises(ReaderConversionError, match='运行目录'):
        resolve_reader_runtime(runtime, '..\\outside.exe')

    with pytest.raises(ReaderConversionError, match='不存在'):
        resolve_reader_runtime(runtime, 'missing.exe')


def test_reader_runtime_root_honors_explicit_environment_override(tmp_path, monkeypatch):
    configured = tmp_path / 'reader runtimes'
    monkeypatch.setenv('NOTEPADVIEW_READER_RUNTIME_DIR', str(configured))

    assert reader_runtime_root() == configured.resolve()


def test_runtime_resolver_maps_calibre_alias_to_packaged_executable(tmp_path):
    runtime = tmp_path / 'runtime'
    calibre = runtime / 'calibre'
    calibre.mkdir(parents=True)
    executable = calibre / 'ebook-convert.exe'
    executable.write_bytes(b'fake executable')

    assert resolve_reader_runtime(runtime, 'calibre') == executable.resolve()


def test_runtime_resolver_maps_libreoffice_alias_to_packaged_executable(tmp_path):
    runtime = tmp_path / 'runtime'
    program = runtime / 'libreoffice' / 'program'
    program.mkdir(parents=True)
    executable = program / 'soffice.exe'
    executable.write_bytes(b'fake executable')

    assert resolve_reader_runtime(runtime, 'libreoffice') == executable.resolve()
