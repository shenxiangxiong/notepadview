"""在原生 Apple Silicon Mac 上构建应用及 DMG；与 Windows 构建隔离。"""

from pathlib import Path
import hashlib
import importlib.metadata
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packaging_tools.reader_runtime import stage_reader_runtimes

VERSION = '1.0.0'
APP_NAME = 'Notepad View'


def run(*args):
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True)


def validate_host():
    if platform.system() != 'Darwin':
        raise RuntimeError('必须在 macOS 上构建，Windows 不能生成可运行的 macOS 安装包。')
    if platform.machine().lower() != 'arm64':
        raise RuntimeError('需要原生 arm64 Python；请退出 Rosetta/x86_64 终端并重建 arm64 虚拟环境。')
    if int(platform.mac_ver()[0].split('.')[0]) < 13:
        raise RuntimeError('当前 Qt 和 Scintilla 依赖要求 macOS 13 或更高版本。')
    if not (3, 11) <= sys.version_info[:2] < (3, 15):
        raise RuntimeError('需要 Python 3.11–3.14，建议原生 arm64 Python 3.12。')
    for tool in ('sips', 'iconutil', 'hdiutil', 'codesign'):
        if shutil.which(tool) is None:
            raise RuntimeError(f'缺少 macOS 系统工具：{tool}')


def validate_dependencies():
    for name in ('PySide6', 'pyside6-scintilla', 'PyInstaller', 'httpx',
                 'PyYAML', 'cryptography', 'keyring'):
        importlib.metadata.version(name)
    subprocess.run([sys.executable, '-c',
        'import pyside6_scintilla, sqlparse, pygments, regex; '
        'import httpx, yaml, cryptography, keyring; '
        'from PySide6 import QtPdf, QtPdfWidgets, QtWebEngineCore, QtWebEngineWidgets; '
        'from PySide6.QtQml import QJSEngine'],
        cwd=ROOT, check=True)


def require_file(path):
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError(f'构建工具未生成有效文件：{path}')


def collect_licenses(destination):
    destination.mkdir(parents=True)
    # 收录整个构建环境的许可证及元数据，包括 Qt/Chromium 和传递依赖。
    for component in importlib.metadata.distributions():
        name = component.metadata['Name']
        for entry in component.files or ():
            if any(token in str(entry).lower() for token in ('license', 'copying', 'notice')) or entry.name == 'METADATA':
                source = Path(component.locate_file(entry))
                if source.is_file():
                    relative = Path(*(part if part != '..' else '_' for part in entry.parts))
                    target = destination / name / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    candidates = [Path(sys.base_prefix) / 'LICENSE.txt',
                  Path(sys.base_prefix) / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'LICENSE.txt']
    python_license = next((path for path in candidates if path.is_file()), None)
    if python_license is None:
        raise RuntimeError('找不到 Python LICENSE.txt；请使用包含许可证的官方 Python 安装。')
    shutil.copy2(python_license, destination / 'Python-LICENSE.txt')


def make_icon(root, work):
    iconset = work / 'NotepadView.iconset'
    iconset.mkdir()
    source = work / 'brand-1024.png'
    code = '''from pathlib import Path
import sys
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from scripts.brand_assets import find_brand_source, remove_outer_background
foreground = remove_outer_background(QImage(str(find_brand_source(Path(sys.argv[1])))))
image = QImage(1024, 1024, QImage.Format_ARGB32_Premultiplied)
image.fill(Qt.transparent)
scaled = foreground.scaled(976, 976, Qt.KeepAspectRatio, Qt.SmoothTransformation)
painter = QPainter(image)
painter.drawImage((1024-scaled.width())//2, (1024-scaled.height())//2, scaled)
painter.end()
if not image.save(sys.argv[2], 'PNG'):
    raise RuntimeError('无法写入 macOS 图标')
'''
    run(sys.executable, '-c', code, root / 'logo', source)
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            suffix = '@2x' if scale == 2 else ''
            run('sips', '--resampleHeightWidth', str(size * scale), str(size * scale),
                source, '--out', iconset / f'icon_{size}x{size}{suffix}.png')
    icon = work / 'NotepadView.icns'
    run('iconutil', '--convert', 'icns', '--output', icon, iconset)
    require_file(icon)
    return icon


def build(root=ROOT):
    validate_host()
    validate_dependencies()
    root = Path(root).resolve()
    output = root / 'dist' / 'macos'
    work = root / 'build' / 'macos' / ('run-' + uuid.uuid4().hex)
    output.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True)
    checksum = output / 'SHA256SUMS.txt'
    checksum.unlink(missing_ok=True)
    try:
        published_app = output / f'{APP_NAME}.app'
        if published_app.exists():
            raise RuntimeError(f'请先移走上次的应用产物再重试：{published_app}')
        icon = make_icon(root, work)
        run(sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed',
            '--onedir', '--name', APP_NAME, '--target-arch', 'arm64',
            '--osx-bundle-identifier', 'com.notepadview.app',
            '--distpath', work / 'dist', '--workpath', work / 'pyinstaller',
            '--specpath', work, '--icon', icon,
            '--add-data', f'{root / "notepadview" / "assets"}:notepadview/assets',
            '--collect-all', 'pyside6_scintilla', '--collect-submodules', 'sqlparse',
            '--collect-submodules', 'pygments.lexers', '--collect-data', 'pygments',
            '--collect-submodules', 'PySide6.QtQml', '--collect-submodules', 'keyring.backends',
            '--collect-all', 'httpx', '--collect-all', 'yaml', '--collect-all', 'cryptography',
            '--collect-all', 'keyring',
            '--hidden-import', 'PySide6.QtPdf', '--hidden-import', 'PySide6.QtPdfWidgets',
            '--hidden-import', 'PySide6.QtWebEngineCore',
            '--hidden-import', 'PySide6.QtWebEngineWidgets', root / 'main.py')
        app = work / 'dist' / f'{APP_NAME}.app'
        require_file(app / 'Contents' / 'MacOS' / APP_NAME)
        info_path = app / 'Contents' / 'Info.plist'
        require_file(info_path)
        info = plistlib.loads(info_path.read_bytes())
        info.update(LSMinimumSystemVersion='13.0', CFBundleShortVersionString=VERSION,
                    CFBundleVersion=VERSION, CFBundleDisplayName=APP_NAME)
        info_path.write_bytes(plistlib.dumps(info))
        resources = app / 'Contents' / 'Resources'
        resources.mkdir(parents=True, exist_ok=True)
        stage_reader_runtimes(resources, env=os.environ, system='Darwin')
        collect_licenses(resources / 'licenses')
        for name in ('README.md', 'THIRD_PARTY_NOTICES.md', 'requirements.txt'):
            shutil.copy2(root / name, resources / name)
        lock = subprocess.check_output([sys.executable, '-m', 'pip', 'freeze'], text=True)
        (resources / 'dependencies-lock.txt').write_text(lock, encoding='utf-8')
        (resources / 'MACOS_BUILD.txt').write_text(
            '本地 arm64 构建，仅 ad-hoc 签名；无 Developer ID 签名，未经 Apple 公证。\n', encoding='utf-8')
        # 增加资源后重新做本地 ad-hoc 签名；这不代表发行者身份认证。
        run('codesign', '--force', '--deep', '--sign', '-', app)
        run('codesign', '--verify', '--deep', '--strict', app)
        staging = work / 'staging'
        staging.mkdir()
        shutil.copytree(app, staging / app.name, symlinks=True)
        os.symlink('/Applications', staging / 'Applications')
        for name in ('README.md', 'THIRD_PARTY_NOTICES.md'):
            shutil.copy2(root / name, staging / name)
        dmg = work / f'NotepadView-{VERSION}-macOS-arm64.dmg'
        run('hdiutil', 'create', '-volname', APP_NAME, '-srcfolder', staging,
            '-format', 'UDZO', '-ov', dmg)
        require_file(dmg)
        run('hdiutil', 'verify', dmg)
        with dmg.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        shutil.copytree(app, published_app, symlinks=True)
        os.replace(dmg, output / dmg.name)
        checksum.write_text(f'{digest}  {dmg.name}\n', encoding='ascii')
        print(f'已构建：{output}（仅 ad-hoc 签名，未经公证）', flush=True)
        return output
    finally:
        # 只删除本次分配的目录，不清理 Windows 或其他运行的产物。
        shutil.rmtree(work)


if __name__ == '__main__':
    try:
        build()
    except (RuntimeError, subprocess.CalledProcessError, OSError,
            importlib.metadata.PackageNotFoundError) as error:
        print(f'macOS 构建失败：{error}', file=sys.stderr)
        sys.exit(1)
