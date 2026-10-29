import os
from pathlib import Path

os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope='session')
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture
def window(app, tmp_path):
    from notepadview.window import MainWindow
    settings = QSettings(str(tmp_path / 'settings.ini'), QSettings.IniFormat)
    widget = MainWindow(settings=settings)
    widget.show()
    app.processEvents()
    yield widget
    for session in widget.sessions:
        session.document.setModified(False)
    widget.editor.document().setModified(False)
    widget.close()
    app.processEvents()


def test_edit_save_and_reopen(window, tmp_path):
    path = tmp_path / 'note.md'
    window.editor.setPlainText('# New note')
    assert window.save_to(path)
    assert path.read_text(encoding='utf-8') == '# New note'
    assert not window.editor.document().isModified()
    path.write_text('# Reopened', encoding='utf-8')
    assert window.load_path(path)
    assert window.editor.toPlainText() == '# Reopened'


def test_view_modes(window):
    window.set_mode('source')
    assert window.source_panel.isVisible()
    assert not window.preview_panel.isVisible()
    window.set_mode('preview')
    assert not window.source_panel.isVisible()
    assert window.preview_panel.isVisible()
    window.set_mode('split')
    assert window.source_panel.isVisible() and window.preview_panel.isVisible()


def test_startup_is_blank_without_a_document_tab(window):
    assert window.editor.toPlainText() == ''
    assert window.document_tabs.count() == 0
    assert window.current_session is None
    assert window.side_filename.text() == ''
    assert window.path_label.text() == ''


def test_changing_theme_does_not_create_a_document_tab(window):
    window.set_theme('dark')
    assert window.document_tabs.count() == 0
    assert window.current_session is None


def test_new_documents_open_in_independent_tabs_with_undo_state(window):
    window.new_document('md')
    first_index = window.document_tabs.currentIndex()
    window.editor.insertPlainText('# first')
    window.new_document('txt')
    assert window.document_tabs.count() == 2
    assert window.is_plain_text
    window.editor.setPlainText('second text')
    window.document_tabs.setCurrentIndex(first_index)
    assert window.editor.toPlainText() == '# first'
    assert not window.is_plain_text
    assert window.undo_action.isEnabled()
    window.editor.undo()
    assert window.editor.toPlainText() != '# first'
    window.document_tabs.setCurrentIndex(1)
    assert window.editor.toPlainText() == 'second text'
    assert window.is_plain_text
    assert not window.undo_action.isEnabled()


def test_opening_documents_adds_tabs_and_close_respects_unsaved_guard(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    first = tmp_path / 'one.md'
    second = tmp_path / 'two.txt'
    first.write_text('# one', encoding='utf-8')
    second.write_text('two', encoding='utf-8')
    assert window.open_document_path(first)
    assert window.open_document_path(second)
    assert window.document_tabs.count() == 2
    window.editor.insertPlainText(' modified')
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args, **kwargs: QMessageBox.Cancel)
    assert not window.close_document_tab(window.document_tabs.currentIndex())
    assert window.document_tabs.count() == 2


def test_theme_is_persisted(window):
    window.theme_actions['dark'].trigger()
    assert window.settings.value('theme') == 'dark'
    assert window.theme == 'dark'
    assert window.theme_actions['dark'].isChecked()
    assert all(action.isChecked() == (theme == 'dark')
               for theme, action in window.theme_actions.items())
    window.theme_actions['light'].trigger()
    assert window.settings.value('theme') == 'light'
    assert window.theme == 'light'
    assert all(action.isChecked() == (theme == 'light')
               for theme, action in window.theme_actions.items())


@pytest.mark.parametrize('mode', ['source', 'split', 'preview'])
def test_view_actions_switch_immediately_without_persisting(window, tmp_path, mode):
    from PySide6.QtCore import QSettings
    assert window.mode == 'source'
    assert window.default_view == 'source'
    assert window.view_actions['source'].isChecked()
    assert window.view_actions[mode] in window.settings_menu.actions()
    window.view_actions[mode].trigger()
    assert window.settings.value('default_view') is None
    assert window.default_view == 'source'
    assert window.mode == mode
    assert window.source_panel.isVisible() == (mode != 'preview')
    assert window.preview_panel.isVisible() == (mode != 'source')
    assert all(action.isChecked() == (key == mode)
               for key, action in window.view_actions.items())

    settings = QSettings(str(tmp_path / 'settings.ini'), QSettings.IniFormat)
    reopened = type(window)(settings=settings)
    try:
        assert reopened.mode == 'source'
        assert reopened.default_view == 'source'
        assert all(action.isChecked() == (key == 'source')
                   for key, action in reopened.view_actions.items())
    finally:
        reopened.editor.document().setModified(False)
        reopened.close()


@pytest.mark.parametrize('saved_mode', ['unknown', 'split', 'preview'])
def test_saved_view_is_ignored_on_startup(window, saved_mode):
    from PySide6.QtCore import QSettings
    settings = QSettings(str(window.settings.fileName() + '.invalid'), QSettings.IniFormat)
    settings.setValue('default_view', saved_mode)
    reopened = type(window)(settings=settings)
    assert reopened.mode == 'source'
    assert reopened.default_view == 'source'
    assert reopened.view_actions['source'].isChecked()
    reopened.editor.document().setModified(False)
    reopened.close()


def test_formatting_from_preview_returns_to_source(window):
    window.new_document('md')
    window.set_mode('preview')
    window.wrap_selection('**', '**', 'word')
    assert window.mode == 'source'
    assert window.source_panel.isVisible()
    assert not window.preview_panel.isVisible()
    assert window.editor.toPlainText() == '**word**'


def test_switching_text_tabs_always_returns_to_source(window):
    window.new_document('txt')
    first = window.current_session
    window.set_mode('split')
    window.new_document('python')
    second = window.current_session
    assert window.mode == 'source'
    window.set_mode('preview')
    window.activate_document(window._session_tab_index(first))
    assert window.mode == 'source'
    window.set_mode('split')
    window.activate_document(window._session_tab_index(second))
    assert window.mode == 'source'
    snapshot = window.workspace.snapshot()
    assert all('view_mode' not in item and 'last_single_view' not in item
               for item in snapshot['documents'])


def test_find_replace_and_undo(window):
    window.editor.setPlainText('one two one')
    window.find_input.setText('one')
    window.replace_input.setText('three')
    window.replace_all()
    assert window.editor.toPlainText() == 'three two three'
    window.editor.undo()
    assert window.editor.toPlainText() == 'one two one'


def test_formatting_wraps_selection(window):
    window.editor.setPlainText('word')
    window.editor.selectAll()
    window.wrap_selection('**', '**', 'bold')
    assert window.editor.toPlainText() == '**word**'


def test_rendered_preview_loads(window, app):
    from PySide6.QtTest import QSignalSpy
    from PySide6.QtTest import QTest
    spy = QSignalSpy(window.preview.loadFinished)
    window.editor.setPlainText('# Preview check')
    window.render_preview()
    for _ in range(150):
        QTest.qWait(100)
        if any(spy.at(i)[0] for i in range(spy.count())):
            break
    assert any(spy.at(i)[0] for i in range(spy.count()))
    texts = []
    window.preview.page().toPlainText(texts.append)
    for _ in range(50):
        if texts:
            break
        QTest.qWait(100)
    assert texts and 'Preview check' in texts[0]


def test_html_export_resolves_relative_images(window, tmp_path):
    source = tmp_path / 'docs' / 'note.md'
    source.parent.mkdir()
    source.write_text('![image](pic.png)', encoding='utf-8')
    window.load_path(source)
    export = tmp_path / 'export.html'
    assert window.export_to(export)
    html = export.read_text(encoding='utf-8')
    assert source.parent.as_uri() in html


def test_toolbar_regions_leave_room_for_document(window, app):
    from PySide6.QtTest import QTest
    window.resize(900, 620)
    window.show_find()
    QTest.qWait(800)
    assert window.find_bar.height() <= 180
    assert window.preview.height() >= 250


def test_save_preserves_nonbreaking_spaces(window, tmp_path):
    source = tmp_path / 'nbsp.md'
    original = 'first\u00a0second\nthird\u00a0fourth\n'
    source.write_text(original, encoding='utf-8')
    window.load_path(source)
    window.save_to(source)
    assert source.read_text(encoding='utf-8') == original


def test_txt_documents_use_literal_mode_and_can_switch_back(window, tmp_path):
    from PySide6.QtTest import QSignalSpy
    from PySide6.QtTest import QTest
    plain = tmp_path / 'note.txt'
    plain.write_text('# literal\n**not formatting**', encoding='utf-8')
    assert window.load_path(plain)
    assert window.is_plain_text
    assert window.editor.language == 'txt'
    assert all(not action.isEnabled() for action in window.format_actions)
    assert window.outline.count() == 0
    spy = QSignalSpy(window.preview.loadFinished)
    window.render_preview()
    for _ in range(100):
        if any(spy.at(index)[0] for index in range(spy.count())):
            break
        QTest.qWait(100)
    rendered = []
    window.preview.page().toPlainText(rendered.append)
    QTest.qWait(100)
    assert rendered and '# literal' in rendered[0] and '**not formatting**' in rendered[0]
    assert window.save_to(plain)
    assert plain.read_text(encoding='utf-8') == '# literal\n**not formatting**'

    markdown = tmp_path / 'note.md'
    assert window.save_to(markdown)
    assert not window.is_plain_text
    assert window.editor.language == 'md'
    assert all(action.isEnabled() for action in window.format_actions)


def test_new_plain_text_document_starts_in_txt_mode(window):
    window.new_plain_text_action.trigger()
    assert window.is_plain_text
    assert '未命名.txt' in window.windowTitle()
    assert not window.editor.document().isModified()


@pytest.mark.parametrize('language', ['md', 'txt', 'json', 'xml', 'sql'])
def test_document_type_and_format_action(window, language):
    window.new_document(language)
    assert window.current_session.document_format == language
    assert window.editor.language == language
    assert window.document_tabs.tabText(window.document_tabs.currentIndex()).endswith('.' + language)
    assert window.format_document_action.isEnabled() == (language in ('json', 'xml', 'sql'))


def test_format_action_is_single_undo(window):
    window.new_document('json')
    source = '{"中文":1}'
    window.editor.setPlainText(source)
    window.format_document()
    assert '  "中文": 1' in window.editor.source_text()
    window.editor.undo()
    assert window.editor.source_text() == source


def test_new_type_chooser_and_cancel(window, monkeypatch):
    from notepadview.dialogs import NewDocumentDialog
    from PySide6.QtCore import QTimer
    def select_json():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, NewDocumentDialog)
        dialog.type_list.setCurrentRow(2)
        dialog.create_button.click()
    QTimer.singleShot(50, select_json)
    window.new_action.trigger()
    assert window.current_session.document_format == 'json'
    QTimer.singleShot(50, lambda: QApplication.activeModalWidget().reject())
    window.add_document_tab_button.click()
    assert window.document_tabs.count() == 1


def test_all_standard_dialog_buttons_are_chinese(window):
    from PySide6.QtWidgets import QDialogButtonBox, QInputDialog, QMessageBox
    box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Discard |
                          QDialogButtonBox.Cancel | QDialogButtonBox.Ok |
                          QDialogButtonBox.Open | QDialogButtonBox.Yes | QDialogButtonBox.No, window)
    expected = {QDialogButtonBox.Save: '保存', QDialogButtonBox.Discard: '不保存',
                QDialogButtonBox.Cancel: '取消', QDialogButtonBox.Ok: '确定',
                QDialogButtonBox.Open: '打开', QDialogButtonBox.Yes: '是', QDialogButtonBox.No: '否'}
    for standard, text in expected.items():
        assert text in box.button(standard).text()
    prompt = QInputDialog(window)
    assert '确定' in prompt.okButtonText()
    assert '取消' in prompt.cancelButtonText()
    warning = QMessageBox(QMessageBox.Warning, '保存更改', '尚未保存',
                          QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, window)
    assert '保存' in warning.button(QMessageBox.Save).text()
    assert '不保存' in warning.button(QMessageBox.Discard).text()
    assert '取消' in warning.button(QMessageBox.Cancel).text()
    from PySide6.QtWidgets import QFileDialog, QPushButton
    file_dialog = QFileDialog(window, '打开文档')
    file_dialog.setOption(QFileDialog.DontUseNativeDialog)
    labels = [button.text() for button in file_dialog.findChildren(QPushButton)]
    assert '打开' in labels and '取消' in labels


def test_new_document_dialog_is_keyboard_accessible(window):
    from notepadview.dialogs import NewDocumentDialog
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialog
    dialog = NewDocumentDialog(window)
    dialog.show()
    QTest.qWait(40)
    assert dialog.type_list.count() == 5
    assert dialog.selected_format() == 'md'
    assert dialog.create_button.text() == '新建'
    assert dialog.cancel_button.text() == '取消'
    dialog.type_list.setFocus()
    QTest.keyClick(dialog.type_list, Qt.Key_Down)
    assert dialog.selected_format() == 'txt'
    QTest.keyClick(dialog.type_list, Qt.Key_Return)
    assert dialog.result() == QDialog.Accepted
    assert not dialog.isVisible()
    dialog.deleteLater()


def test_find_replace_stacks_inputs_and_can_be_hidden_without_losing_text(window):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    assert not window.find_bar.isVisible()
    window.show_find()
    QTest.qWait(40)
    assert window.find_input.mapTo(window.find_bar, window.find_input.rect().topLeft()).y() < window.replace_input.mapTo(window.find_bar, window.replace_input.rect().topLeft()).y()
    assert window.find_action.isChecked()
    window.find_input.setText('search')
    window.replace_input.setText('replacement')
    window.find_bar.close()
    assert not window.find_bar.isVisible()
    assert not window.find_action.isChecked()
    window.find_action.trigger()
    assert window.find_bar.isVisible()
    assert window.find_input.text() == 'search'
    assert window.replace_input.text() == 'replacement'
    QTest.keyClick(window.find_input, Qt.Key_Escape)
    assert not window.find_bar.isVisible()
    assert not window.find_action.isChecked()


def test_find_replace_uses_an_embedded_bar(window):
    window.set_mode('source')
    window.show_find()
    assert not window.find_bar.isWindow()
    assert window.find_bar.parentWidget() is window.source_panel
    window.hide_find()


def test_cursor_status_update_uses_cached_text_statistics(window, monkeypatch):
    window.editor.setPlainText('one two three')
    window.update_statistics()

    def fail_if_scanned():
        raise AssertionError('cursor movement must not scan the whole document')

    monkeypatch.setattr(window.editor, 'source_text', fail_if_scanned)
    window.update_status()
    assert '13 字符' in window.stats_label.text()


def test_large_preview_loads_complete_content_and_ignores_stale_result(window):
    window.new_document('json')
    window.editor.setPlainText('not json 中文 & < >\n' * 30000 + 'large-preview-end')
    window.render_preview()
    wait_until(lambda: window._preview_ready, timeout=30000)
    assert window.preview.url().isLocalFile()
    assert preview_js(window, 'document.body.textContent.endsWith("large-preview-end")')
    window.render_preview()
    window.new_document('txt')
    window.editor.setPlainText('new-document-only')
    window.render_preview()
    wait_until(lambda: window._preview_work is None)
    wait_until(lambda: window._preview_ready)
    assert preview_js(window, 'document.body.textContent') == 'new-document-only'


def test_long_tabs_remain_compact_and_have_chinese_close_tooltips(window):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTabBar
    window.new_document('json')
    window.current_session.path = window.path = Path('D:/documents/' + '长文件名' * 30 + '.json')
    window.update_title()
    assert window.document_tabs.elideMode() == Qt.ElideMiddle
    assert window.document_tabs.usesScrollButtons()
    assert window.document_tabs.tabRect(0).width() <= 280
    assert window.document_tabs.tabRect(0).height() <= 42
    assert not window.document_tabs.tabIcon(0).isNull()
    assert str(window.path) in window.document_tabs.tabToolTip(0)
    close = window.document_tabs.tabButton(0, QTabBar.RightSide)
    assert close is not None and '关闭' in close.toolTip()


def test_soft_theme_persists_and_colors_editor_and_real_preview(window, tmp_path):
    from notepadview.palettes import THEMES
    from pyside6_scintilla import Scintilla
    from PySide6.QtGui import QColor
    assert window.theme_button.menu() is window.theme_menu
    assert window.theme_actions['soft'] in window.theme_menu.actions()
    window.theme_actions['soft'].trigger()
    assert window.theme == 'soft'
    assert window.settings.value('theme') == 'soft'
    assert all(action.isChecked() == (theme == 'soft')
               for theme, action in window.theme_actions.items())
    color = QColor(THEMES['soft']['bg'])
    expected = color.red() | color.green() << 8 | color.blue() << 16
    assert window.editor.styleBack(Scintilla.StylesCommon.Default) == expected
    wait_until(lambda: window._preview_ready and window._preview_target is None)
    assert preview_js(window, 'getComputedStyle(document.documentElement).backgroundColor') == f'rgb({color.red()}, {color.green()}, {color.blue()})'
    settings = QSettings(str(tmp_path / 'settings.ini'), QSettings.IniFormat)
    reopened = type(window)(settings=settings)
    try:
        assert reopened.theme == 'soft'
        assert all(action.isChecked() == (theme == 'soft')
                   for theme, action in reopened.theme_actions.items())
    finally:
        reopened.close()


def test_file_dialogs_use_themed_qt_widgets(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    options = []
    def cancel_dialog(*args, **kwargs):
        options.append(kwargs.get('options', QFileDialog.Option(0)))
        return '', ''
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', cancel_dialog)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', cancel_dialog)
    window.open_document()
    window.save_as()
    window.export_html()
    window.insert_image()
    assert len(options) == 4
    assert all(option & QFileDialog.DontUseNativeDialog for option in options)


@pytest.mark.parametrize('language', ['md', 'sql'])
def test_soft_theme_code_background_is_not_overridden_by_highlighting(window, language):
    window.new_document(language)
    source = '~~~sql\n-- comment\nselect 1;\n~~~' if language == 'md' else '-- comment\nselect 1;'
    window.editor.setPlainText(source)
    window.set_theme('soft')
    wait_until(lambda: window._preview_ready and window._preview_target is None)
    assert preview_js(window, 'getComputedStyle(document.querySelector(".highlight")).backgroundColor') == 'rgb(221, 232, 222)'


def test_find_next_from_preview_shows_match_after_find_panel_is_hidden(window):
    window.editor.setPlainText('first second first')
    window.show_find()
    window.find_input.setText('second')
    window.hide_find()
    window.set_mode('preview')
    assert window.find_next()
    assert window.editor.selected_text() == 'second'
    assert window.mode == 'preview'
    assert window.preview_panel.isVisible()
    assert not window.find_bar.isVisible()


def test_reordered_tabs_keep_document_state_and_cancel_close(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    for language, text in [('txt', 'one'), ('json', '{"value":2}'), ('sql', 'select 3;')]:
        window.new_document(language)
        window.editor.setPlainText(text)
    window.document_tabs.moveTab(0, 2)
    window.document_tabs.setCurrentIndex(2)
    assert window.editor.source_text() == 'one'
    window.editor.insertPlainText('changed ')
    assert window.save_to(tmp_path / 'one.txt')
    assert (tmp_path / 'one.txt').read_text(encoding='utf-8') == 'changed one'
    window.document_tabs.setCurrentIndex(0)
    assert window.editor.source_text() == '{"value":2}'
    window.editor.insertPlainText(' ')
    window.document_tabs.setCurrentIndex(2)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a, **kw: QMessageBox.Cancel)
    assert not window.close_document_tab(0)
    assert window.document_tabs.currentIndex() == 2
    assert window.editor.source_text() == 'changed one'


def test_dark_dropdown_frame_does_not_keep_a_white_background(window, app, tmp_path):
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QComboBox, QFileDialog
    previous_style = app.style().objectName()
    review = None
    try:
        app.setStyle('Fusion')
        review = type(window)(settings=QSettings(str(tmp_path / 'dropdown.ini'), QSettings.IniFormat))
        review.show()
        review.set_theme('dark')
        review.theme_menu.popup(review.theme_button.mapToGlobal(review.theme_button.rect().bottomLeft()))
        QTest.qWait(60)
        assert review.theme_menu.isVisible()
        image = review.theme_menu.grab().toImage()
        review.theme_menu.hide()
        assert image.pixelColor(image.width() // 2, 1).lightness() < 160
        file_dialog = QFileDialog(review, '打开文档', str(tmp_path), '文档 (*.md);;所有文件 (*)')
        file_dialog.setOption(QFileDialog.DontUseNativeDialog)
        file_dialog.show()
        QTest.qWait(50)
        for combo in file_dialog.findChildren(QComboBox):
            combo.showPopup()
            QTest.qWait(30)
            image = combo.view().window().grab().toImage()
            combo.hidePopup()
            assert image.pixelColor(image.width() // 2, 1).lightness() < 160
        file_dialog.reject()
    finally:
        if review is not None:
            review.close()
        app.setStyle(previous_style)


def test_tab_switch_preserves_selection_when_new_tab_is_created(window):
    window.new_document('txt')
    window.editor.setPlainText('first document')
    window.editor.set_selection(3, 8)
    window.new_document('sql')
    window.editor.setPlainText('select 1;')
    window.document_tabs.setCurrentIndex(0)
    assert window.editor.selection_range() == (3, 8)
    assert window.editor.selected_text() == 'st do'
    window.document_tabs.setCurrentIndex(1)
    assert window.editor.source_text() == 'select 1;'


@pytest.mark.parametrize('language', ['json', 'xml', 'sql'])
def test_save_as_adds_selected_extension(window, tmp_path, monkeypatch, language):
    from PySide6.QtWidgets import QFileDialog
    window.new_document(language)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **kw: (
        str(tmp_path / 'new-file'), language.upper() + ' (*.' + language + ')'))
    assert window.save_as()
    assert (tmp_path / ('new-file.' + language)).exists()
    assert window.editor.language == language


def test_code_preview_in_webengine(window):
    from PySide6.QtTest import QTest
    window.new_document('xml')
    window.editor.setPlainText('<script>alert(1)</script>')
    window.render_preview()
    rendered = []
    for _ in range(80):
        QTest.qWait(100)
        if not rendered:
            window.preview.page().toPlainText(rendered.append)
        if rendered and '<script>alert(1)</script>' in rendered[-1]:
            break
        rendered.clear()
    assert rendered and '<script>alert(1)</script>' in rendered[-1]


def test_release_smoke_formats_and_saves_all_code_types(window, tmp_path):
    from main import exercise_editing
    result = exercise_editing(window, tmp_path)
    assert result['chinese_context_menus']
    assert result['window_controls']
    assert result['document_rename']
    assert result['formats'] == ['json', 'xml', 'sql']
    assert result['column_editing']
    assert result['file_drop']
    assert '"中文": 1' in (tmp_path / 'sample.json').read_text(encoding='utf-8')
    assert 'SELECT' in (tmp_path / 'renamed.sql').read_text(encoding='utf-8')


def test_rectangle_survives_tab_switch_without_covering_intervening_text(window):
    from pyside6_scintilla import Scintilla
    from PySide6.QtTest import QTest
    window.new_document('txt')
    window.editor.setPlainText('abc\ndef\nghi')
    window.editor.setSelectionMode(Scintilla.SelectionMode.Rectangle)
    window.editor.setRectangularSelectionAnchor(0)
    window.editor.setRectangularSelectionCaret(9)
    window.new_document('sql')
    window.document_tabs.setCurrentIndex(0)
    assert window.editor.selectionIsRectangle()
    assert window.editor.selections() == 3
    QTest.keyClicks(window.editor, 'X')
    assert window.editor.source_text() == 'Xbc\nXef\nXhi'


def test_save_cannot_overwrite_path_owned_by_another_tab(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    path = tmp_path / 'owned.txt'
    path.write_text('first', encoding='utf-8')
    window.open_document_path(path)
    window.new_document('txt')
    window.editor.setPlainText('second')
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a, **kw: QMessageBox.Ok)
    assert not window.save_to(path)
    assert path.read_text(encoding='utf-8') == 'first'
    assert window.current_session.path is None


def test_markdown_wrap_does_not_collapse_rectangle(window):
    from pyside6_scintilla import Scintilla
    window.new_document('md')
    window.editor.setPlainText('abc\ndef\nghi')
    window.editor.setSelectionMode(Scintilla.SelectionMode.Rectangle)
    window.editor.setRectangularSelectionAnchor(0)
    window.editor.setRectangularSelectionCaret(9)
    window.wrap_selection('**', '**', 'bold')
    assert window.editor.source_text() == 'abc\ndef\nghi'


def test_closed_placeholder_documents_are_released(window, app):
    from PySide6.QtCore import QCoreApplication, QEvent
    from notepadview.editor import SourceDocument
    for _ in range(5):
        window.new_document('txt')
        window.close_document_tab(0)
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert len(window.findChildren(SourceDocument)) <= 1


def test_invalid_format_preserves_document_and_locates_error(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    window.new_document('json')
    window.editor.setPlainText('{invalid')
    window.editor.set_selection(1, 4)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a, **kw: QMessageBox.Ok)
    window.format_document()
    assert window.editor.source_text() == '{invalid'
    assert window.editor.selection_range() == (1, 1)
    assert not window.editor.document().isModified()


def test_scroll_controls_are_available_for_both_panes(window):
    assert window.editor_scroll_top_button.toolTip() == '编辑区到顶'
    assert window.editor_scroll_bottom_button.toolTip() == '编辑区到底'
    assert window.preview_scroll_top_button.toolTip() == '预览区到顶'
    assert window.preview_scroll_bottom_button.toolTip() == '预览区到底'
    window.editor.setPlainText('\n'.join(f'line {index}' for index in range(200)))
    window.editor.verticalScrollBar().setValue(0)
    assert window.editor.verticalScrollBar().maximum() > 0
    window.editor_scroll_top_button.click()
    assert window.editor.verticalScrollBar().value() == window.editor.verticalScrollBar().minimum()
    window.editor_scroll_bottom_button.click()
    assert window.editor.verticalScrollBar().value() == window.editor.verticalScrollBar().maximum()
    window.preview_scroll_top_button.click()
    window.preview_scroll_bottom_button.click()
    assert window.editor_scroll_top_button.parent().parent() is window.source_panel
    assert window.preview_scroll_top_button.parent().parent() is window.preview_panel
    assert window.editor_scroll_controls.x() > window.source_panel.width() // 2
    assert window.preview_scroll_controls.x() > window.preview_panel.width() // 2
    assert not hasattr(window, 'recent_list')
    assert window.directory_label.text() == '目录'
    assert 'QWidget#previewPanel' in window.styleSheet()


def wait_until(predicate, timeout=8000):
    from time import monotonic
    from PySide6.QtTest import QTest
    deadline = monotonic() + timeout / 1000
    while monotonic() < deadline:
        if predicate():
            return
        QTest.qWait(20)
    assert predicate(), 'Timed out waiting for preview navigation'


def preview_js(window, script):
    from PySide6.QtWebEngineCore import QWebEngineScript
    result = []
    window.preview.page().runJavaScript(script, QWebEngineScript.ApplicationWorld, result.append)
    wait_until(lambda: bool(result))
    return result[0]


def load_long_preview(window, language='md'):
    window.new_document(language)
    window.editor.setPlainText('\n\n'.join(f'paragraph {index}' for index in range(180)))
    window.render_preview()
    wait_until(lambda: 'paragraph 179' in (preview_js(window, 'document.body.innerText') or ''))
    wait_until(lambda: preview_js(window, 'document.documentElement.scrollHeight > innerHeight'))


@pytest.mark.parametrize('language', ['md', 'txt', 'json', 'xml', 'sql'])
@pytest.mark.parametrize('pane', ['preview', 'editor'])
def test_scroll_buttons_really_move_both_panes(window, language, pane):
    from PySide6.QtWebEngineCore import QWebEngineSettings
    load_long_preview(window, language)
    getattr(window, pane + '_scroll_bottom_button').click()
    wait_until(lambda: preview_js(window, 'Math.abs(scrollY + innerHeight - document.documentElement.scrollHeight) < 2'))
    bar = window.editor.verticalScrollBar()
    assert bar.value() == bar.maximum() > 0
    getattr(window, pane + '_scroll_top_button').click()
    wait_until(lambda: preview_js(window, 'scrollY === 0'))
    assert bar.value() == bar.minimum()
    assert not window.preview.settings().testAttribute(QWebEngineSettings.JavascriptEnabled)


def test_directory_click_positions_source_and_preview(window):
    from PySide6.QtCore import Qt
    window.new_document('md')
    prefix = '# Same\n\n' + 'paragraph\n\n' * 100
    source = prefix + 'Same\n----\n\n  ## **中文**\n\n' + 'tail\n\n' * 80
    window.editor.setPlainText(source)
    window.render_preview()
    wait_until(lambda: '中文' in (preview_js(window, 'document.body.innerText') or ''))
    assert window.outline.count() == 3
    assert window.outline.item(2).text().strip() == '中文'
    window.set_mode('preview')
    item = window.outline.item(1)
    line = prefix.count('\n')
    assert item.data(Qt.UserRole) == line
    window.outline.itemClicked.emit(item)
    assert window.editor.cursor_position()[0] == line + 1
    wait_until(lambda: preview_js(window, f'Math.abs(document.getElementById("heading-{line}").getBoundingClientRect().top - 16) < 2'))
    assert window.mode == 'preview'


def test_navigation_survives_pending_render_and_theme_reload(window):
    load_long_preview(window)
    window.editor.insertPlainText('extra\n')
    assert window.timer.isActive()
    window.preview_scroll_bottom_button.click()
    wait_until(lambda: preview_js(window, 'Math.abs(scrollY + innerHeight - document.documentElement.scrollHeight) < 2'))
    window.set_theme('dark')
    window.preview_scroll_top_button.click()
    wait_until(lambda: preview_js(window, 'scrollY === 0 && getComputedStyle(document.documentElement).backgroundColor === "rgb(32, 33, 36)"'))
    assert window.editor.verticalScrollBar().value() == 0


@pytest.mark.parametrize('mode', ['source', 'split', 'preview'])
def test_directory_navigation_uses_updated_headings_during_render(window, mode):
    load_long_preview(window)
    window.set_mode(mode)
    prefix = '# First\n\n' + 'paragraph\n\n' * 100
    line = prefix.count('\n')
    window.editor.setPlainText(prefix + '## Updated\n\n' + 'tail\n\n' * 80)
    assert window.timer.isActive()
    window.outline.itemClicked.emit(window.outline.item(1))
    assert window.editor.cursor_position()[0] == line + 1
    wait_until(lambda: preview_js(window, f'(() => {{ const h = document.getElementById("heading-{line}"); return h && h.textContent === "Updated" && Math.abs(h.getBoundingClientRect().top - 16) < 2; }})()'))
    assert window.mode == mode


def test_pending_navigation_does_not_leak_to_new_tab(window):
    load_long_preview(window)
    window.set_theme('dark')
    window.scroll_preview_bottom()
    window.new_document('txt')
    window.editor.setPlainText('\n'.join(f'new tab {index}' for index in range(150)))
    window.render_preview()
    wait_until(lambda: 'new tab 149' in (preview_js(window, 'document.body.innerText') or ''))
    wait_until(lambda: window._preview_ready and window._preview_target is None)
    assert preview_js(window, 'scrollY') == 0


def test_theme_reload_restores_existing_preview_scroll(window):
    load_long_preview(window)
    preview_js(window, 'window.scrollTo(0, 600)')
    wait_until(lambda: window.scroll_position == 600)
    window.set_theme('dark')
    wait_until(lambda: window._preview_ready and window._preview_target is None)
    assert preview_js(window, 'scrollY') == 600


def test_removed_heading_cannot_block_later_scroll_restoration(window):
    load_long_preview(window)
    prefix = '# First\n\n' + 'paragraph\n\n' * 100
    window.editor.setPlainText(prefix + '## Removed\n\n' + 'tail\n\n' * 80)
    window.render_preview()
    window.outline.itemClicked.emit(window.outline.item(1))
    window.editor.setPlainText(prefix + 'removed heading\n\n' + 'tail\n\n' * 80)
    window.render_preview()
    wait_until(lambda: window._preview_ready and window._preview_target is None)
    preview_js(window, 'window.scrollTo(0, 600)')
    wait_until(lambda: window.scroll_position == 600)
    window.set_theme('dark')
    wait_until(lambda: window._preview_ready and window._preview_target is None)
    assert window._preview_target is None
    assert preview_js(window, 'scrollY') == 600


def test_image_path_encodes_fragment_and_percent(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    window.path = tmp_path / 'note.md'
    window.editor.clear()
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **kw: (str(tmp_path / 'image#1%.png'), ''))
    window.insert_image()
    assert 'image%231%25.png' in window.editor.toPlainText()


def test_network_images_can_load_with_local_base(window):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread
    from PySide6.QtTest import QTest
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.end_headers()
            self.wfile.write((Path(__file__).parents[1] / 'notepadview/assets/desk.png').read_bytes())
        def log_message(self, *_):
            pass
    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        window.editor.setPlainText(f'![image](http://127.0.0.1:{server.server_port}/test.png)')
        window.render_preview()
        for _ in range(80):
            if requests:
                break
            QTest.qWait(100)
        assert '/test.png' in requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
