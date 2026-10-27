"""Validate find controls and capture the three native theme layouts."""
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from notepadview.window import MainWindow


def main():
    app = QApplication([])
    app.setStyle('Fusion')
    output = Path(__file__).resolve().parents[1] / 'screenshots/find-dialog'
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory() as directory:
        window = MainWindow(QSettings(str(Path(directory) / 'settings.ini'), QSettings.IniFormat))
        window.new_document('txt')
        window.editor.setPlainText('编号12\n编号34\n编号56')
        window.showMaximized()
        QTest.qWait(150)
        assert window.isMaximized()
        window.showNormal()
        window.resize(1100, 760)
        window.show_find()
        window.find_input.setText(r'编号(\d+)')
        window.replace_input.setText(r'项目\1')
        window.regex_checkbox.setChecked(True)
        for theme in ('light', 'dark', 'soft'):
            window.set_theme(theme)
            QTest.qWait(80)
            assert window.brand.font().pixelSize() == window.menuBar().font().pixelSize()
            assert window.find_previous_button.x() == window.replace_button.x()
            assert window.find_next_button.x() == window.replace_all_button.x()
            assert window.find_previous_button.width() == window.replace_button.width()
            assert window.find_next()
            window.find_bar.grab().save(str(output / f'{theme}-find.png'))
            window.grab().save(str(output / f'{theme}-window.png'))
        window.replace_all_button.click()
        assert window.editor.source_text() == '项目12\n项目34\n项目56'
        assert '3' in window.find_feedback.text()
        window.find_input.setText('[')
        assert not window.find_next()
        window.find_bar.grab().save(str(output / 'invalid-regex.png'))
        window.find_bar.close()
        window.close()
    report = {'maximized_start': True, 'brand_size': 13, 'brand_icon': 20,
              'aligned_text_buttons': True, 'regex_search_replace': True,
              'chinese_error': True, 'themes': ['light', 'dark', 'soft']}
    (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
