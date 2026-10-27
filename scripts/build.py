from pathlib import Path
import importlib.metadata
import hashlib
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packaging_tools.reader_runtime import prepare_windows_runtimes, stage_reader_runtimes


def run(*args):
    environment = dict(os.environ)
    if args[:2] == ('-m', 'PyInstaller'):
        windows = Path(os.environ.get('SystemRoot', 'C:/Windows'))
        # Exclude unrelated native toolchains that can shadow Windows ICU and CRT.
        environment['PATH'] = os.pathsep.join(map(str, [Path(sys.executable).parent,
            Path(sys.base_prefix), Path(sys.base_prefix) / 'DLLs', windows / 'System32', windows]))
    subprocess.run([sys.executable, *args], cwd=ROOT, env=environment, check=True)


def build():
    runtime_env = prepare_windows_runtimes(ROOT)
    run('scripts/create_assets.py')
    run('-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed', '--name', 'NotepadView',
        '--icon', 'notepadview/assets/app.ico', '--add-data', 'notepadview/assets;notepadview/assets',
        '--collect-all', 'pyside6_scintilla', '--collect-submodules', 'sqlparse',
        '--collect-submodules', 'pygments.lexers', '--collect-data', 'pygments',
        '--collect-submodules', 'PySide6.QtQml', '--collect-submodules', 'keyring.backends',
        '--collect-all', 'httpx', '--collect-all', 'yaml', '--collect-all', 'cryptography',
        '--collect-all', 'keyring', 'main.py')
    assemble_setup(runtime_env=runtime_env)


def assemble_setup(*, runtime_env=None):
    runtime_env = prepare_windows_runtimes(ROOT) if runtime_env is None else runtime_env
    distribution = ROOT / 'dist' / 'NotepadView'
    stage_reader_runtimes(distribution, env=runtime_env, system='Windows')
    licenses = distribution / '_internal/licenses'
    licenses.mkdir(parents=True, exist_ok=True)
    for name in ['PySide6', 'PySide6_Essentials', 'PySide6_Addons', 'pyside6-scintilla', 'sqlparse',
                 'shiboken6', 'markdown-it-py', 'mdurl', 'mdit-py-plugins', 'Pygments', 'regex',
                 'httpx', 'httpcore', 'h11', 'certifi', 'idna', 'PyYAML', 'cryptography',
                 'cffi', 'keyring', 'pywin32-ctypes', 'jaraco.classes', 'PyInstaller',
                 'python-docx', 'openpyxl', 'lxml', 'et_xmlfile', 'typing_extensions', 'PyMuPDF']:
        component = importlib.metadata.distribution(name)
        for entry in component.files or []:
            if any(token in str(entry).lower() for token in ['license', 'copying', 'notice']) or entry.name == 'METADATA':
                source = Path(component.locate_file(entry))
                if source.is_file():
                    target = licenses / name / str(entry).replace('..', '_')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.exists():
        shutil.copy2(python_license, licenses / 'Python-LICENSE.txt')
    lock = subprocess.check_output([sys.executable, '-m', 'pip', 'freeze'], text=True)
    (ROOT / 'dependencies-lock.txt').write_text(lock, encoding='utf-8')
    shutil.copy2(ROOT / 'dependencies-lock.txt', distribution / 'dependencies-lock.txt')
    shutil.copy2(ROOT / 'packaging_tools/uninstall.ps1', distribution / 'uninstall.ps1')
    shutil.copy2(ROOT / 'README.md', distribution / 'README.md')
    shutil.copy2(ROOT / 'THIRD_PARTY_NOTICES.md', distribution / 'THIRD_PARTY_NOTICES.md')
    payload = ROOT / 'build/payload.tar.xz'
    payload.parent.mkdir(parents=True, exist_ok=True)
    portable = ROOT / 'dist/NotepadView-1.0.0-Windows-x64-portable.zip'
    from packaging_tools.payload import write_payload
    write_payload(distribution, payload)
    # 便携版保留 Windows 资源管理器兼容的 Deflate。
    with zipfile.ZipFile(portable, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as portable_archive:
        for path in sorted(distribution.rglob('*')):
            if path.is_file():
                relative = path.relative_to(distribution).as_posix()
                portable_archive.write(path, relative)
    run('-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed',
        '--name', 'NotepadView-1.0.0-Setup', '--icon', 'notepadview/assets/app.ico',
        '--add-data', 'build/payload.tar.xz;.', '--add-data', 'notepadview/assets;notepadview/assets',
        'packaging_tools/setup.py')
    artifacts = ['NotepadView-1.0.0-Setup.exe', 'NotepadView-1.0.0-Windows-x64-portable.zip']
    checksums = []
    for name in artifacts:
        with (ROOT / 'dist' / name).open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        checksums.append(f'{digest}  {name}')
    (ROOT / 'dist/SHA256SUMS.txt').write_text('\n'.join(checksums) + '\n', encoding='ascii')
    print('Built installer: dist/NotepadView-1.0.0-Setup.exe', flush=True)


if __name__ == '__main__':
    if '--prepare-runtimes' in sys.argv:
        prepare_windows_runtimes(ROOT)
    elif '--setup-only' in sys.argv:
        assemble_setup()
    else:
        build()
