import json
import os
from pathlib import Path, PurePosixPath
import shutil
import uuid
import zipfile

from packaging_tools.file_associations import register_file_associations

APP_NAME = 'Notepad View'
INSTALL_DIR_NAME = 'NotepadView'
EXECUTABLE_NAME = 'NotepadView.exe'


def validate_target(target):
    target = Path(target).resolve()
    if target == Path(target.anchor):
        raise ValueError('请选择应用程序的子目录，不要直接安装到磁盘根目录。')
    return target


MANIFEST_NAME = 'notepadview-install.json'
UNINSTALL_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\NotepadView'


def _relative_file(name):
    if not isinstance(name, str) or not name:
        raise ValueError('安装清单包含无效路径。')
    relative = PurePosixPath(name.replace('\\', '/'))
    if (relative.is_absolute() or not relative.parts or
            any(part in ('.', '..') or part.endswith((' ', '.')) or ':' in part
                for part in relative.parts)):
        raise ValueError('安装清单包含无效路径：' + name)
    return Path(*relative.parts)


def _installed_files(target):
    try:
        if not target.is_dir() or target.is_symlink() or target.is_junction():
            return None
        manifest_path = target / MANIFEST_NAME
        if manifest_path.is_symlink() or not manifest_path.is_file():
            return None
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest.get('app') != 'NotepadView' or not isinstance(manifest.get('files'), list):
            return None
        files = [_relative_file(name) for name in manifest['files']]
        if Path(EXECUTABLE_NAME) not in files:
            return None
        # 不跟随旧目录中的链接，避免升级触及安装目录以外的数据。
        if any(path.is_symlink() or path.is_junction() for path in target.rglob('*')):
            return None
        if any((target / name).exists() and not (target / name).is_file() for name in files):
            return None
        with (target / EXECUTABLE_NAME).open('rb') as executable:
            if executable.read(2) != b'MZ':
                return None
        return files
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def default_install_target(registry=None):
    fallback = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'Programs' / INSTALL_DIR_NAME
    try:
        if registry is None:
            import winreg as registry
        with registry.OpenKey(registry.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            location, kind = registry.QueryValueEx(key, 'InstallLocation')
        if kind == registry.REG_SZ and isinstance(location, str) and location.strip():
            target = validate_target(location)
            if _installed_files(target) is not None:
                return target
    except (ImportError, OSError, ValueError, TypeError):
        pass
    return fallback


def _check_replaceable(target, files):
    if os.name != 'nt':
        return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    for name in files:
        path = target / name
        if not path.exists():
            continue
        handle = create(str(path), 0x10000, 7, None, 3, 0x80, None)
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        close(handle)


def _replace_directory(source, target):
    import time
    for attempt in range(15):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == 14:
                raise
            time.sleep(1)


def install_payload(archive_path, target, progress=None):
    target = validate_target(target)
    old_files = None
    if target.exists():
        if not target.is_dir():
            raise FileExistsError('所选位置不是目录，请选择其他目录。')
        if any(target.iterdir()):
            old_files = _installed_files(target)
            if old_files is None:
                raise FileExistsError('所选目录不是空目录，且不是有效的本应用安装，请选择其他目录。')
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / ('.notepadview-install-' + uuid.uuid4().hex)
    backup = target.parent / ('.notepadview-backup-' + uuid.uuid4().hex)
    backed_up = False
    try:
        from packaging_tools.payload import open_payload
        with open_payload(archive_path) as archive:
            try:
                entries = [(entry, _relative_file(entry.filename)) for entry in archive.infolist()]
            except ValueError as error:
                raise ValueError('安装包包含无效路径。') from error
            files = [str(name).replace('\\', '/') for entry, name in entries if not entry.is_dir()]
            if EXECUTABLE_NAME not in files:
                raise ValueError(f'安装包中缺少 {EXECUTABLE_NAME}，请重新下载安装包。')
            if old_files is not None:
                _check_replaceable(target, old_files + [Path(MANIFEST_NAME)])
                shutil.copytree(target, staging)
                for name in old_files + [Path(MANIFEST_NAME)]:
                    (staging / name).unlink(missing_ok=True)
            else:
                staging.mkdir()
            for index, (entry, name) in enumerate(entries):
                destination = staging / name
                if entry.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    if destination.exists():
                        raise FileExistsError('安装包与保留的用户文件冲突：' + str(name))
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(entry) as source, destination.open('xb') as output:
                        shutil.copyfileobj(source, output)
                if progress:
                    progress(int((index + 1) * 90 / max(len(entries), 1)))
            (staging / MANIFEST_NAME).write_text(json.dumps({
                'app': 'NotepadView', 'version': '1.0.0', 'files': files,
            }, ensure_ascii=True, indent=2), encoding='utf-8')
        if target.exists():
            _replace_directory(target, backup)
            backed_up = True
        try:
            _replace_directory(staging, target)
        except OSError:
            if backed_up:
                _replace_directory(backup, target)
                backed_up = False
            raise
    except OSError as error:
        if isinstance(error, PermissionError) or getattr(error, 'winerror', None) in (32, 33):
            raise PermissionError('无法替换旧版本文件，请关闭本应用及占用安装目录的程序后重试。') from error
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    if backed_up:
        # 发布完成后清理备份失败时保留备份，不将已成功发布的版本报告为失败。
        shutil.rmtree(backup, ignore_errors=True)


def register_install(target, desktop=True):
    import subprocess
    import winreg
    target = Path(target).resolve()
    key_path = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\NotepadView'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        values = {
            'DisplayName': APP_NAME, 'DisplayVersion': '1.0.0',
            'Publisher': APP_NAME, 'InstallLocation': str(target),
            'DisplayIcon': str(target / EXECUTABLE_NAME),
            'UninstallString': 'powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + str(target / 'uninstall.ps1') + '"',
        }
        for name, value in values.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(key, 'NoModify', 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, 'NoRepair', 0, winreg.REG_DWORD, 1)
    register_file_associations(target, winreg)
    script = '''$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
$paths = @([Environment]::GetFolderPath('Programs'))
if ($env:NOTEPADVIEW_DESKTOP -eq '1') { $paths += [Environment]::GetFolderPath('Desktop') }
foreach ($folder in $paths) {
    $link = $shell.CreateShortcut((Join-Path $folder 'Notepad View.lnk'))
    $link.TargetPath = Join-Path $env:NOTEPADVIEW_TARGET $env:NOTEPADVIEW_EXECUTABLE
    $link.IconLocation = $link.TargetPath + ",0"
    $link.WorkingDirectory = $env:NOTEPADVIEW_TARGET
    $link.Save()
}
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class NotepadViewAssociationShell {
    [DllImport("shell32.dll")]
    public static extern void SHChangeNotify(uint eventId, uint flags, IntPtr item1, IntPtr item2);
}
'@
[NotepadViewAssociationShell]::SHChangeNotify(0x08000000, 0, [IntPtr]::Zero, [IntPtr]::Zero)
'''
    environment = dict(os.environ, NOTEPADVIEW_TARGET=str(target), NOTEPADVIEW_DESKTOP='1' if desktop else '0',
                       NOTEPADVIEW_EXECUTABLE=EXECUTABLE_NAME)
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                   env=environment, check=True, capture_output=True,
                   creationflags=subprocess.CREATE_NO_WINDOW)
