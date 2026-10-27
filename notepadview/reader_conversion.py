"""Bounded subprocess conversion and content-addressed reader cache."""
from hashlib import sha256
import os
from pathlib import Path
import subprocess
import tempfile
from threading import Lock

from notepadview.reader_runtime import resolve_reader_runtime


class ReaderConversionError(ValueError):
    pass


class ReaderConversionService:
    def __init__(self, cache_root, runtime_root, timeout_seconds=120,
                 max_output_bytes=128 * 1024 * 1024, runner=None):
        if timeout_seconds <= 0 or max_output_bytes <= 0:
            raise ValueError('转换超时和输出上限必须大于零。')
        self.cache_root = Path(cache_root)
        self.runtime_root = Path(runtime_root)
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.runner = subprocess.run if runner is None else runner
        self._locks_guard = Lock()
        self._locks = {}

    def _source_digest(self, source):
        digest = sha256()
        with source.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        return digest.hexdigest()

    def _cache_key(self, source, executable, suffix):
        engine_stat = executable.stat()
        identity = '\0'.join((str(source.resolve()), self._source_digest(source),
                              str(executable.resolve()), str(engine_stat.st_size),
                              str(engine_stat.st_mtime_ns), suffix.casefold()))
        return sha256(identity.encode('utf-8')).hexdigest()

    def _key_lock(self, key):
        with self._locks_guard:
            return self._locks.setdefault(key, Lock())

    def convert(self, source, engine, suffix, command_factory):
        source = Path(source).resolve()
        if not source.is_file():
            raise ReaderConversionError(f'源文件不存在或不可读取：{source}')
        suffix = suffix.casefold()
        if not suffix.startswith('.') or suffix == '.':
            raise ValueError('输出扩展名必须以点开头。')
        executable = resolve_reader_runtime(self.runtime_root, engine)
        key = self._cache_key(source, executable, suffix)
        self.cache_root.mkdir(parents=True, exist_ok=True)
        destination = self.cache_root / f'{key}{suffix}'
        with self._key_lock(key):
            if destination.is_file() and 0 < destination.stat().st_size <= self.max_output_bytes:
                return destination
            if destination.exists():
                destination.unlink()
            self._convert_to(source, executable, suffix, command_factory, destination)
        return destination

    def _convert_to(self, source, executable, suffix, command_factory, destination):
        try:
            with tempfile.TemporaryDirectory(prefix='reader-', dir=self.cache_root) as work:
                output_dir = Path(work)
                command = command_factory(executable, source, output_dir)
                self.runner(command, check=True, capture_output=True, text=True,
                            timeout=self.timeout_seconds, cwd=str(output_dir))
                outputs = [path for path in output_dir.rglob('*')
                           if path.is_file() and path.suffix.casefold() == suffix]
                if not outputs:
                    raise ReaderConversionError(f'转换程序未生成 {suffix} 文件。')
                if len(outputs) != 1:
                    raise ReaderConversionError(f'转换程序生成了多个 {suffix} 文件，无法确定预览结果。')
                output = outputs[0]
                size = output.stat().st_size
                if size == 0:
                    raise ReaderConversionError('转换程序生成了空文件。')
                if size > self.max_output_bytes:
                    raise ReaderConversionError('转换结果超过允许的大小上限。')
                os.replace(output, destination)
        except subprocess.TimeoutExpired as error:
            raise ReaderConversionError('文件转换超时。') from error
        except subprocess.CalledProcessError as error:
            details = error.stderr or error.stdout or ''
            if isinstance(details, bytes):
                details = details.decode('utf-8', errors='replace')
            details = ' '.join(str(details).split())[:2000]
            message = f'转换程序失败（退出码 {error.returncode}）'
            if details:
                message += f'：{details}'
            raise ReaderConversionError(message) from error
        except OSError as error:
            raise ReaderConversionError(f'无法启动或读取转换程序：{error}') from error
