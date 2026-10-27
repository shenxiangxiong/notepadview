"""Stage platform-native LibreOffice and Calibre payloads beside the app."""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import tempfile


_EXECUTABLES = {
    'Windows': {
        'libreoffice': Path('program') / 'soffice.exe',
        'calibre': Path('ebook-convert.exe'),
    },
    'Darwin': {
        'libreoffice': Path('LibreOffice.app') / 'Contents' / 'MacOS' / 'soffice',
        'calibre': Path('calibre.app') / 'Contents' / 'MacOS' / 'ebook-convert',
    },
}
_ENVIRONMENT = {
    'libreoffice': 'NOTEPADVIEW_LIBREOFFICE_DIR',
    'calibre': 'NOTEPADVIEW_CALIBRE_DIR',
}


_WINDOWS_DOWNLOADS = {
    'libreoffice': {
        'version': '26.2.6',
        'url': 'https://download.documentfoundation.org/libreoffice/stable/26.2.6/win/x86_64/LibreOffice_26.2.6_Win_x86-64.msi',
        'sha256': 'f9877032fd908beb9c0ddf06df4af5c2e85f419c42e14876c4cce5aae5fb2660',
        'source': 'https://download.documentfoundation.org/libreoffice/src/26.2.6/',
    },
    'calibre': {
        'version': '9.17.0',
        'url': 'https://download.calibre-ebook.com/9.17.0/calibre-64bit-9.17.0.msi',
        'sha256': 'a91ea9b26af6cd057d8b193b86edb66d1535f7c5cb0a106965fa74f8168a6bd5',
        'source': 'https://download.calibre-ebook.com/9.17.0/calibre-9.17.0.tar.xz',
    },
}


def _retrieve(url, destination):
    urls = [url]
    if url.startswith('https://download.documentfoundation.org/libreoffice/'):
        urls.append(url.replace('https://download.documentfoundation.org/',
                                'https://mirrors.nju.edu.cn/tdf/', 1))
    for index, candidate in enumerate(urls):
        try:
            subprocess.run([
                'curl.exe', '--fail', '--location', '--retry', '3',
                '--connect-timeout', '30', '--max-time', '1800',
                '--output', str(destination), candidate,
            ], check=True)
            return
        except subprocess.CalledProcessError:
            if index == len(urls) - 1:
                raise
            print('下载源不可用，尝试备用镜像（仍执行相同 SHA256 校验）。', flush=True)


def _verified_download(url, target, expected):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)

    def valid(path):
        if not path.is_file():
            return False
        with path.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest() == expected

    if valid(target):
        return target
    partial = target.with_suffix(target.suffix + '.part')
    try:
        print(f'下载阅读引擎：{url}', flush=True)
        _retrieve(url, partial)
        if not valid(partial):
            raise RuntimeError(f'阅读引擎下载 SHA256 校验失败：{target.name}')
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return target


def _download_runtime(engine, cache):
    info = _WINDOWS_DOWNLOADS[engine]
    installer = _verified_download(info['url'], cache / 'downloads' / info['url'].rsplit('/', 1)[1], info['sha256'])
    destination = cache / engine
    print(f'解压阅读引擎：{engine} {info["version"]}（不安装到系统）', flush=True)
    with tempfile.TemporaryDirectory(prefix=f'{engine}-', dir=cache, ignore_cleanup_errors=True) as temporary:
        extracted = Path(temporary)
        log = cache / f'{engine}-extract.log'
        result = subprocess.run([
            'msiexec.exe', '/a', str(installer), '/qn',
            f'TARGETDIR={extracted}', '/L*v', str(log),
        ], check=False)
        if result.returncode not in (0, 3010):
            raise RuntimeError(f'{engine} 解压失败（{result.returncode}），日志：{log}')
        relative = _EXECUTABLES['Windows'][engine]
        matches = [path for path in extracted.rglob(relative.name) if path.is_file()]
        sources = [path.parents[len(relative.parts) - 1] for path in matches
                   if path.parts[-len(relative.parts):] == relative.parts]
        if len(sources) != 1:
            raise RuntimeError(f'{engine} 安装包内未找到唯一完整运行时：{relative}')
        source = sources[0]
        # Administrative MSI extraction may leave the MSI alongside its payload.
        for msi in source.glob('*.msi'):
            msi.unlink()
        (source / 'runtime-origin.json').write_text(
            json.dumps(info, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        with tempfile.TemporaryDirectory(prefix=f'{engine}-ready-', dir=cache) as ready:
            staged = Path(ready) / engine
            shutil.copytree(source, staged)
            if destination.exists():
                shutil.rmtree(destination)
            staged.replace(destination)
    return destination


def prepare_windows_runtimes(root, *, env=None):
    environment = dict(os.environ if env is None else env)
    cache = Path(root).resolve() / 'build' / 'reader-runtimes'
    cache.mkdir(parents=True, exist_ok=True)
    for engine, variable in _ENVIRONMENT.items():
        configured = environment.get(variable)
        source = Path(configured).resolve() if configured else cache / engine
        executable = source / _EXECUTABLES['Windows'][engine]
        if configured and not executable.is_file():
            raise RuntimeError(f'{variable} 缺少所需可执行文件：{executable}')
        if not executable.is_file():
            source = _download_runtime(engine, cache)
        if not (source / _EXECUTABLES['Windows'][engine]).is_file():
            raise RuntimeError(f'阅读引擎准备失败：{engine}')
        environment[variable] = str(source)
        print(f'阅读引擎已就绪：{engine} -> {source}', flush=True)
    return environment


def stage_reader_runtimes(distribution, *, env=None, system=None):
    env = os.environ if env is None else env
    system = platform.system() if system is None else system
    if system not in _EXECUTABLES:
        raise RuntimeError(f'不支持打包阅读引擎的平台：{system}')
    distribution = Path(distribution).resolve()
    runtime_root = distribution / 'reader-runtimes'
    runtime_root.mkdir(parents=True, exist_ok=True)

    sources = {}
    for engine, variable in _ENVIRONMENT.items():
        configured = env.get(variable)
        if not configured:
            raise RuntimeError(f'缺少阅读引擎运行时，请设置 {variable}。')
        source = Path(configured).resolve()
        if not source.is_dir():
            raise RuntimeError(f'{variable} 指向的运行时目录不存在：{source}')
        executable = source / _EXECUTABLES[system][engine]
        if not executable.is_file():
            raise RuntimeError(f'{variable} 缺少所需可执行文件：{executable}')
        sources[engine] = source

    for engine, source in sources.items():
        destination = runtime_root / engine
        if destination.is_symlink():
            raise RuntimeError(f'拒绝覆盖符号链接运行时目录：{destination}')
        if destination.exists():
            def remove_readonly(function, path, error):
                if not isinstance(error, PermissionError) or os.stat(path).st_mode & stat.S_IWRITE:
                    raise error
                os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
                function(path)
            shutil.rmtree(destination, onexc=remove_readonly)
        shutil.copytree(source, destination)
    return runtime_root
