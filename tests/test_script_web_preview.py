import json
import time

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox
from PySide6.QtWebEngineCore import QWebEngineScript, QWebEngineSettings

from notepadview.text_types import type_for_path
from test_ui import app, window  # noqa: F401


def page_value(window, expression):
    result = []
    window.preview.page().runJavaScript(expression, result.append)
    deadline = time.monotonic() + 5
    while not result and time.monotonic() < deadline:
        QTest.qWait(20)
    assert result
    return result[0]


@pytest.mark.parametrize('language', ['md', 'txt', 'python'])
def test_nv_preview_uses_uniform_deep_blue_background(window, language):
    window.new_document(language)
    window.editor.setPlainText('# Preview' if language == 'md' else 'preview')
    window.set_theme('light')
    window.set_theme('nv')
    deadline = time.monotonic() + 5
    colors = None
    while time.monotonic() < deadline:
        QTest.qWait(30)
        result = []
        window.preview.page().runJavaScript('''JSON.stringify([
            getComputedStyle(document.documentElement).backgroundColor,
            document.body && getComputedStyle(document.body).backgroundColor
        ])''', QWebEngineScript.ApplicationWorld, result.append)
        callback_deadline = time.monotonic() + 1
        while not result and time.monotonic() < callback_deadline:
            QTest.qWait(10)
        colors = json.loads(result[0]) if result and result[0] else None
        if colors == ['rgb(28, 50, 83)', 'rgb(28, 50, 83)']:
            break
    assert colors == ['rgb(28, 50, 83)', 'rgb(28, 50, 83)']


def test_powershell_type_and_run_visibility(window):
    assert type_for_path('hello.PS1') == 'powershell'
    for language in ('python', 'powershell', 'html', 'js', 'txt'):
        window.new_document(language)
        assert window.run_button.isVisible() == (language in ('python', 'powershell'))


def test_run_saves_current_content_and_uses_file_directory(window, tmp_path, monkeypatch):
    from notepadview import script_runner
    path = tmp_path / '中文 script.py'
    window.new_document('python')
    window.editor.setPlainText('print("new")')
    calls = []
    monkeypatch.setattr(window, 'save_document', lambda: window.save_to(path))
    monkeypatch.setattr(script_runner, 'launch_script', lambda path, language: calls.append((path, language)))
    assert window.run_script()
    assert path.read_text(encoding='utf-8') == 'print("new")'
    assert calls == [(path.resolve(), 'python')]


def test_run_cancel_or_launch_error(window, monkeypatch):
    from notepadview import script_runner
    window.new_document('python')
    monkeypatch.setattr(window, 'save_document', lambda: False)
    monkeypatch.setattr(script_runner, 'launch_script', lambda *args: pytest.fail('must not launch'))
    assert not window.run_script()
    window.new_document('txt')
    assert not window.run_script()


def test_html_preview_executes_inline_css_js_and_restores_other_types(window):
    window.new_document('html')
    window.editor.setPlainText('<!DOCTYPE html><HTML><HEAD><style>#result { color: rgb(12, 34, 56); }</style></HEAD><BODY><div id="result">before</div><script>document.getElementById("result").textContent="executed";</script></BODY></HTML>')
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        QTest.qWait(50)
        if page_value(window, 'document.getElementById("result")?.textContent') == 'executed':
            break
    assert page_value(window, 'document.getElementById("result").textContent') == 'executed'
    assert page_value(window, 'getComputedStyle(document.getElementById("result")).color') == 'rgb(12, 34, 56)'
    assert window.preview.settings().testAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls)
    window.new_document('md')
    assert not window.preview.settings().testAttribute(QWebEngineSettings.JavascriptEnabled)


def test_online_styles_and_scripts_load(window):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            css = self.path == '/style.css'
            payload = (b'#remote { color: rgb(21, 43, 65); }' if css else
                       b'document.getElementById("remote").textContent="online";')
            self.send_response(200)
            self.send_header('Content-Type', 'text/css' if css else 'application/javascript')
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f'http://127.0.0.1:{server.server_port}'
        window.new_document('html')
        window.editor.setPlainText(f'<link rel="stylesheet" href="{base}/style.css"><div id="remote">before</div><script src="{base}/script.js"></script>')
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            QTest.qWait(50)
            if page_value(window, 'document.getElementById("remote")?.textContent') == 'online':
                break
        assert page_value(window, 'document.getElementById("remote").textContent') == 'online'
        assert page_value(window, 'getComputedStyle(document.getElementById("remote")).color') == 'rgb(21, 43, 65)'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_terminal_command_quotes_paths_and_uses_script_directory(tmp_path, monkeypatch):
    import base64
    import subprocess
    from notepadview import script_runner
    path = tmp_path / "中文 space ' $ script.ps1"
    path.write_text('Write-Output ok', encoding='utf-8')
    monkeypatch.setattr(script_runner.shutil, 'which', lambda name: 'powershell.exe')
    calls = []
    monkeypatch.setattr(subprocess, 'Popen', lambda args, **kwargs: calls.append((args, kwargs)))
    script_runner.launch_script(path, 'powershell')
    args, options = calls[0]
    script = base64.b64decode(args[-1]).decode('utf-16-le')
    assert script == "& '" + str(path.resolve()).replace("'", "''") + "'"
    assert '-NoExit' in args
    assert options['cwd'] == str(tmp_path)
    assert options['creationflags'] == subprocess.CREATE_NEW_CONSOLE


def test_python_launch_command_executes_saved_script(tmp_path, monkeypatch):
    import subprocess
    import sys
    from notepadview import script_runner
    path = tmp_path / "中文 script's.py"
    path.write_text('from pathlib import Path\nPath("result.txt").write_text("executed", encoding="utf-8")\n', encoding='utf-8')
    original_popen = subprocess.Popen

    def bounded_process(args, **kwargs):
        args = [arg for arg in args if arg != '-NoExit']
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        return original_popen(args, **kwargs, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    monkeypatch.setattr(script_runner, 'python_command', lambda path: [sys.executable])
    monkeypatch.setattr(subprocess, 'Popen', bounded_process)
    process = script_runner.launch_script(path, 'python')
    try:
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, (stdout, stderr)
        assert (tmp_path / 'result.txt').read_text(encoding='utf-8') == 'executed'
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_launch_failure_is_reported(window, tmp_path, monkeypatch):
    from notepadview import script_runner
    window.new_document('python')
    assert window.save_to(tmp_path / 'script.py')
    def fail(*args):
        raise OSError('missing interpreter')
    monkeypatch.setattr(script_runner, 'launch_script', fail)
    errors = []
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: errors.append(args))
    assert not window.run_script()
    assert errors


def test_large_html_worker_keeps_real_markup():
    from notepadview.window import MainWindow
    from threading import Event
    work = {'source': '<h1>web</h1>', 'language': 'html', 'theme': 'dark', 'done': Event()}
    MainWindow._render_preview_work(work)
    assert '<h1>web</h1>' in work['html']
    assert '&lt;h1' not in work['html']
