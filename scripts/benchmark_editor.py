"""Measure editor operations and event-loop latency without user settings."""
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import perf_counter, sleep

os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from notepadview.window import MainWindow


def benchmark():
    app = QApplication([])
    app.setStyle('Fusion')
    app.setFont(QFont('Microsoft YaHei UI', 10))
    root = Path(__file__).resolve().parents[1]
    output = root / 'screenshots' / 'performance-refresh'
    output.mkdir(parents=True, exist_ok=True)
    results = {}
    with TemporaryDirectory() as directory:
        settings = QSettings(str(Path(directory) / 'settings.ini'), QSettings.IniFormat)
        window = MainWindow(settings=settings)
        window.show()
        window.new_document('json')
        source = 'This is not JSON. 中文内容 & <example>\n' * 24000
        app.clipboard().setText(source)
        started = perf_counter()
        window.editor.paste()
        results['invalid_json_paste_ms'] = round((perf_counter() - started) * 1000, 2)
        results['payload_bytes'] = len(source.encode('utf-8'))
        assert window.editor.source_text().replace('\r\n', '\n') == source
        gaps = []
        previous = perf_counter()

        def tick():
            nonlocal previous
            now = perf_counter()
            gaps.append((now - previous) * 1000)
            previous = now

        pulse = QTimer()
        pulse.timeout.connect(tick)
        pulse.start(10)
        deadline = perf_counter() + 20
        while window.editor.document().highlight_key != (window.editor.document().revision, 'json'):
            if perf_counter() > deadline:
                print('Highlight state:', window.editor.document().revision,
                      window.editor.document().highlight_key,
                      window.editor._highlight_work is not None,
                      window.editor._pending_highlight[3] if window.editor._pending_highlight else None)
                raise AssertionError('Highlight did not finish')
            QTest.qWait(10)
            sleep(.001)
        pulse.stop()
        results['highlight_and_ui_seconds'] = round(perf_counter() - started, 3)
        results['maximum_event_loop_gap_ms'] = round(max(gaps, default=0), 2)
        started = perf_counter()
        for _ in range(1000):
            window.update_status()
        results['1000_cursor_updates_ms'] = round((perf_counter() - started) * 1000, 2)
        window.new_document('txt')
        window.editor.setPlainText('small document')
        started = perf_counter()
        window.document_tabs.setCurrentIndex(0)
        results['switch_to_large_document_ms'] = round((perf_counter() - started) * 1000, 2)
        path = Path(directory) / 'large.json'
        path.write_text(source, encoding='utf-8')
        started = perf_counter()
        window.open_document_path(path)
        results['open_large_document_ms'] = round((perf_counter() - started) * 1000, 2)
        window.new_document('md')
        window.editor.setPlainText('# Notepad View\n\n## 工作记录\n\n优化后的文档编辑器。\n\n- 查找与替换\n- 多文档切换\n')
        window.show_find()
        window.find_input.setText('文档')
        window.replace_input.setText('内容')
        for theme in ('light', 'dark', 'soft'):
            window.set_theme(theme)
            QTest.qWait(350)
            window.grab().save(str(output / (theme + '-window.png')))
            window.find_bar.grab().save(str(output / (theme + '-find.png')))
        for session in window.sessions:
            session.document.setModified(False)
        window.close()
        app.processEvents()
    (output / 'benchmark.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    benchmark()
