"""Resolve reader engines only from the application-managed runtime directory."""
import os
from pathlib import Path
import sys


_ENGINE_PATHS = {
    'win32': {
        'libreoffice': Path('libreoffice') / 'program' / 'soffice.exe',
        'calibre': Path('calibre') / 'ebook-convert.exe',
    },
    'darwin': {
        'libreoffice': Path('libreoffice') / 'LibreOffice.app' / 'Contents' /
                       'MacOS' / 'soffice',
        'calibre': Path('calibre') / 'calibre.app' / 'Contents' /
                   'MacOS' / 'ebook-convert',
    },
}


def reader_runtime_root():
    configured = os.environ.get('NOTEPADVIEW_READER_RUNTIME_DIR')
    if configured:
        return Path(configured).resolve()
    executable = Path(sys.executable).resolve()
    if getattr(sys, 'frozen', False):
        if sys.platform == 'darwin':
            return executable.parents[1] / 'Resources' / 'reader-runtimes'
        return executable.parent / 'reader-runtimes'
    return Path(__file__).resolve().parents[1] / 'build' / 'reader-runtimes'


def resolve_reader_runtime(root, engine):
    from notepadview.reader_conversion import ReaderConversionError

    root = Path(root).resolve()
    engine_path = _ENGINE_PATHS.get(sys.platform, {}).get(engine, Path(engine))
    candidate = (root / engine_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ReaderConversionError('阅读引擎路径必须位于应用运行目录内。') from error
    if not candidate.is_file():
        raise ReaderConversionError(f'阅读引擎不存在：{candidate.name}')
    return candidate
