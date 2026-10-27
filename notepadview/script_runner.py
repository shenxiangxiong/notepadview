"""通过独立终端执行已保存的脚本。"""
import base64
import os
from pathlib import Path
import shutil
import subprocess
import sys


def python_command(path):
    for parent in (path.parent, *path.parent.parents):
        candidate = parent / '.venv' / 'Scripts' / 'python.exe'
        if candidate.is_file():
            return [str(candidate)]
    if not getattr(sys, 'frozen', False):
        return [sys.executable]
    for name, args in (('py', ['-3']), ('python', [])):
        executable = shutil.which(name)
        if executable:
            return [executable, *args]
    raise OSError('未找到 Python，请安装 Python 或在脚本项目中创建 .venv。')


def launch_script(path, language):
    path = Path(path).resolve()
    if language not in ('python', 'powershell'):
        raise ValueError('仅支持运行 Python 和 PowerShell。')
    if os.name != 'nt':
        raise OSError('独立终端运行目前仅支持 Windows。')
    terminal = shutil.which('pwsh') or shutil.which('powershell')
    if not terminal:
        raise OSError('未找到 PowerShell。')
    command = (python_command(path) + ['-u', str(path)] if language == 'python'
               else [str(path)])
    # PowerShell 单引号字面量避免路径中的空格、引号和特殊字符变成命令。
    script = '& ' + ' '.join("'" + item.replace("'", "''") + "'" for item in command)
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    return subprocess.Popen([terminal, '-NoLogo', '-NoProfile', '-NoExit',
                             '-EncodedCommand', encoded], cwd=str(path.parent),
                            creationflags=subprocess.CREATE_NEW_CONSOLE)
