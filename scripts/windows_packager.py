"""Console entry point for the double-click Windows packaging tool."""
from pathlib import Path
import os
import subprocess
import sys


def project_root():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def package(root, *, check_only=False):
    python = root / '.venv' / 'Scripts' / 'python.exe'
    script = root / 'scripts' / 'build.py'
    for path in (python, script, root / 'main.py'):
        if not path.is_file():
            print(f'缺少文件：{path}\n请将打包工具放在项目根目录，并准备好项目 .venv 环境。')
            return 1
    if check_only:
        print(f'项目检查通过：{root}')
        return 0
    build_dir = root / 'build'
    build_dir.mkdir(exist_ok=True)
    log_path = build_dir / 'package-latest.log'
    environment = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
    print(f'正在打包：{root}\n日志：{log_path}\n', flush=True)
    with log_path.open('w', encoding='utf-8') as log:
        with subprocess.Popen([str(python), str(script)], cwd=root, env=environment,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding='utf-8', errors='replace') as process:
            for line in process.stdout:
                print(line, end='', flush=True)
                log.write(line)
                log.flush()
            result = process.wait()
    if result:
        print(f'\n打包失败（退出码 {result}），请查看日志：{log_path}')
        return result
    print(f'\n打包完成！安装包、便携版和 SHA256 校验文件位于：\n{root / "dist"}')
    return 0


def main():
    check_only = '--check' in sys.argv[1:]
    try:
        result = package(project_root(), check_only=check_only)
    except (OSError, ValueError) as error:
        print(f'打包工具运行失败：{error}')
        result = 1
    if not check_only:
        try:
            input('\n按回车键关闭窗口……')
        except (EOFError, KeyboardInterrupt):
            pass
    return result


if __name__ == '__main__':
    raise SystemExit(main())
