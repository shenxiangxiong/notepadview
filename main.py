import os
import json
import multiprocessing
from pathlib import Path
import sys
import time

os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')

from PySide6.QtCore import QMimeData, QPoint, QPointF, QSettings, QStandardPaths, QTimer, QUrl, Qt
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QFont
from PySide6.QtWidgets import QApplication, QMessageBox
from notepadview.window import MainWindow
from notepadview.localization import install_chinese_translations


def exercise_editing(window, target):
    """Exercise bundled dependencies without touching the user's documents/settings."""
    from pyside6_scintilla import Scintilla
    target.mkdir(parents=True, exist_ok=True)
    samples = {'json': '{"中文":1}', 'xml': '<root><item>中文</item></root>',
               'sql': "select userName, 'select' from Users where id=42"}
    for language, source in samples.items():
        window.new_document(language)
        window.editor.setPlainText(source)
        assert window.format_document()
        formatted = window.editor.source_text()
        assert formatted != source
        window.editor.undo()
        assert window.editor.source_text() == source
        window.editor.redo()
        assert window.editor.source_text() == formatted
        assert window.save_to(target / ('sample.' + language))
    window.new_document('txt')
    window.editor.setPlainText('abc\ndef\nghi')
    window.editor.setSelectionMode(Scintilla.SelectionMode.Rectangle)
    window.editor.setRectangularSelectionAnchor(0)
    window.editor.setRectangularSelectionCaret(9)
    assert window.editor.selectionIsRectangle() and window.editor.selections() == 3
    window.editor.cut()
    assert window.editor.source_text() == 'bc\nef\nhi'
    window.editor.undo()
    assert window.editor.source_text() == 'abc\ndef\nghi'
    dragged = target / 'dragged.md'
    dragged.write_text('# 拖放自检\n\n文件已通过启动检查打开。', encoding='utf-8')
    mime = QMimeData()
    final_path = target / 'sample.sql'
    mime.setUrls([QUrl.fromLocalFile(str(dragged)), QUrl.fromLocalFile(str(final_path))])
    actions = Qt.CopyAction | Qt.MoveAction
    enter = QDragEnterEvent(QPoint(10, 10), actions, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(window, enter)
    assert enter.isAccepted()
    move = QDragMoveEvent(QPoint(10, 10), actions, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(window, move)
    assert move.isAccepted()
    drop = QDropEvent(QPointF(10, 10), actions, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(window, drop)
    assert drop.isAccepted() and drop.dropAction() == Qt.CopyAction
    deadline = time.monotonic() + 2
    while window.path != final_path.resolve() and time.monotonic() < deadline:
        QApplication.processEvents()
    assert window.path == final_path.resolve() and len(window.sessions) == 5
    assert any(session.path == dragged.resolve() for session in window.sessions)
    assert window.editor.source_text() == final_path.read_text(encoding='utf-8')
    renamed = target / 'renamed.sql'
    assert window.rename_document_session(window.current_session, renamed.name)
    assert not final_path.exists() and window.path == renamed.resolve()
    draft = next(session for session in window.sessions if session.path is None)
    assert window.rename_document_session(draft, '列编辑草稿.txt')
    assert draft.name == '列编辑草稿.txt'
    assert window.windowFlags() & Qt.FramelessWindowHint
    assert window.menuBar().cornerWidget(Qt.TopRightCorner) is not None
    assert not hasattr(window, 'theme_combo')
    assert [action.text() for action in window.menuBar().actions()] == ['文件', '编辑', '帮助']
    assert not window.brand_icon.pixmap().isNull()
    assert window.brand.font().pixelSize() == window.menuBar().font().pixelSize() == 13
    assert window.brand_icon.size().width() == window.brand_icon.size().height() == 20
    window.view_actions['source'].trigger()
    assert window.mode == window.settings.value('default_view') == 'source'
    window.view_actions['split'].trigger()
    assert window.split_button.isChecked()
    window.split_button.click()
    assert window.mode == 'source'
    window.set_mode('preview')
    window.split_button.click()
    assert window.mode == 'split'
    window.split_button.click()
    assert window.mode == 'preview'
    window.set_mode('split')
    original = window.editor.source_text()
    window.editor.setPlainText('编号12 编号34')
    window.editor.set_selection(0, 0)
    window.find_input.setText(r'编号(\d+)')
    window.replace_input.setText(r'项目\1')
    window.regex_checkbox.setChecked(True)
    assert window.find_next()
    assert window.editor.selected_text() == '编号12'
    window.replace_all()
    assert window.editor.source_text() == '项目12 项目34'
    window.editor.undo()
    assert window.editor.source_text() == '编号12 编号34'
    window.editor.setPlainText(original)
    window.regex_checkbox.setChecked(False)
    window.find_input.clear()
    window.replace_input.clear()
    assert window.rename_category('', '默认文件')
    window.delete_category('')
    assert window.file_list.topLevelItem(0).text(0) == '默认文件'
    window.theme_actions['soft'].trigger()
    assert window.theme == 'soft' and window.theme_actions['soft'].isChecked()
    window.toggle_sidebar_page('outline')
    assert window.sidebar_stack.currentWidget() is window.outline
    window.toggle_sidebar_page('files')
    assert window.outer_split.widget(0) is window.document_area
    assert window.outer_split.widget(1) is window.sidebar_group
    window.sidebar_position_actions['left'].trigger()
    assert window.outer_split.widget(0) is window.sidebar_group
    assert window.settings.value('sidebar_position') == 'left'
    window.sidebar_position_actions['right'].trigger()
    for session in window.sessions:
        session.document.setModified(False)
    menus = [window.editor.build_context_menu(), window.preview.build_context_menu()]
    try:
        for menu in menus:
            labels = [action.text() for action in menu.actions() if not action.isSeparator()]
            assert labels and all(any('\u4e00' <= char <= '\u9fff' for char in label) for label in labels)
    finally:
        for menu in menus:
            menu.deleteLater()
    return {'formats': list(samples), 'column_editing': True, 'file_drop': True,
            'chinese_context_menus': True, 'window_controls': True, 'document_rename': True,
            'single_header': True, 'right_navigation': True, 'view_preference': True,
            'sidebar_position': True, 'brand_logo': True,
            'regex_search': True, 'regex_replace_undo': True,
            'split_toggle': True, 'editable_default_category': True,
            'tabs': len(window.sessions)}


def main():
    app = QApplication(sys.argv)
    install_chinese_translations(app)
    app.setApplicationName('Notepad View')
    app.setOrganizationName('NotepadView')
    app.setApplicationVersion('1.0.0')
    app.setStyle('Fusion')
    app.setFont(QFont('Microsoft YaHei UI', 10))
    arguments = sys.argv[1:]
    report_path = next((Path(arg.split('=', 1)[1]) for arg in arguments if arg.startswith('--report=')), None)
    settings = QSettings(str(report_path.with_suffix('.ini')), QSettings.IniFormat) if report_path and '--smoke-test' in arguments else None
    instance = None
    if '--smoke-test' not in arguments:
        from notepadview.instance import SingleInstance
        workspace_path = Path(QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation)) / 'Notepad View' / 'workspace.json'
        instance = SingleInstance(workspace_path)
        if not instance.acquire():
            paths = [str(Path(argument).resolve()) for argument in arguments if not argument.startswith('--')]
            if instance.forward(paths):
                return
            QMessageBox.critical(None, '无法启动', '工作区正在使用或无法访问。请稍后重试，避免覆盖已打开文档的缓存。')
            return
        app.aboutToQuit.connect(instance.close)
    window = MainWindow(settings=settings)
    if instance is not None:
        def receive_files(paths):
            for path in paths:
                window.open_document_path(Path(path))
            window.setWindowState(window.windowState() & ~Qt.WindowMinimized)
            window.show()
            window.raise_()
            window.activateWindow()
        instance.filesReceived.connect(receive_files)
    editing_result = {}
    if '--exercise-editing' in arguments:
        if report_path is None:
            raise ValueError('--exercise-editing requires --report')
        editing_result = exercise_editing(window, report_path.parent / 'smoke-documents')
    if '--smoke-test' in arguments:
        window.editor.document().setModified(False)
        completed = False
        def finish_preview(success):
            nonlocal completed
            if not success or completed:
                return
            def inspect(text):
                nonlocal completed
                if completed:
                    return
                ok = window.isMaximized() and 'Notepad View' in window.windowTitle() and (
                    text.strip() == window.editor.source_text().strip() if editing_result else not text.strip())
                if not ok:
                    QTimer.singleShot(100, lambda: finish_preview(True))
                    return
                completed = True
                for arg in arguments:
                    if arg.startswith('--report='):
                        report = Path(arg.split('=', 1)[1])
                        report.parent.mkdir(parents=True, exist_ok=True)
                        report.write_text(json.dumps({'preview_loaded': ok, 'text_length': len(text),
                                                       'maximized': window.isMaximized(), **editing_result}), encoding='utf-8')
                        window.grab().save(str(report.with_suffix('.png')))
                app.exit(0 if ok else 3)
            window.preview.page().toPlainText(inspect)
        window.preview.loadFinished.connect(finish_preview)
        QTimer.singleShot(0, window.render_preview)
        QTimer.singleShot(15000, lambda: app.exit(2))
    elif arguments:
        for argument in arguments:
            if not argument.startswith('--'):
                window.load_path(Path(argument))
    window.showMaximized()
    sys.exit(app.exec())


if __name__ == '__main__':
    multiprocessing.freeze_support()
    main()
