from pathlib import Path
import subprocess

import pytest

from notepadview.ebook_reader import (
    EBOOK_CONVERT_SUFFIXES, convert_ebook_to_epub, ebook_convert_command,
)
from notepadview.reader_conversion import ReaderConversionService


@pytest.mark.parametrize('suffix', ['.mobi', '.azw3', '.fb2'])
def test_calibre_conversion_writes_cached_epub_without_touching_source(tmp_path, suffix):
    runtime = tmp_path / 'runtime'
    calibre = runtime / 'calibre'
    calibre.mkdir(parents=True)
    (calibre / 'ebook-convert.exe').write_bytes(b'fake executable')
    source = tmp_path / ('book with spaces' + suffix)
    original = b'book source bytes'
    source.write_bytes(original)
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        Path(args[-1]).write_bytes(b'epub conversion')
        return subprocess.CompletedProcess(args, 0, '', '')

    service = ReaderConversionService(tmp_path / 'cache', runtime, runner=runner)
    result = convert_ebook_to_epub(source, service)

    assert suffix in EBOOK_CONVERT_SUFFIXES
    assert result.suffix == '.epub'
    assert result.read_bytes() == b'epub conversion'
    assert source.read_bytes() == original
    assert str(source.resolve()) in calls[0]
    assert len(calls) == 1


def test_ebook_convert_command_passes_paths_as_separate_arguments(tmp_path):
    executable = tmp_path / 'ebook-convert.exe'
    source = tmp_path / 'a book.fb2'
    output = tmp_path / 'output'

    command = ebook_convert_command(executable, source, output)

    assert command == [str(executable), str(source.resolve()),
                       str(output / 'a book.epub')]
