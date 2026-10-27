from html.parser import HTMLParser

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QToolButton

from test_ui import app, window  # noqa: F401
from notepadview.palettes import theme_colors


def test_find_bar_can_be_resized_from_left_edge(window, app):
    window.set_mode('source')
    window.find_action.setChecked(True)
    window.toggle_find(True)
    app.processEvents()
    bar = window.find_bar
    right = bar.geometry().right()
    initial_width = bar.width()
    handle = bar.resize_handle
    QTest.mousePress(handle, Qt.LeftButton, pos=QPoint(2, 15))
    QTest.mouseMove(handle, QPoint(-58, 15))
    QTest.mouseRelease(handle, Qt.LeftButton, pos=QPoint(2, 15))
    app.processEvents()
    assert bar.width() > initial_width
    assert bar.geometry().right() == right
    assert window.find_input.width() > 300
    window.hide_find()
    window.find_action.setChecked(True)
    window.toggle_find(True)
    assert bar.width() > initial_width


@pytest.mark.parametrize('mode', ['preview', 'split'])
@pytest.mark.parametrize('opened', [False, True])
def test_ctrl_f_uses_preview_selection(window, app, monkeypatch, mode, opened):
    window.set_mode(mode)
    window.editor.setPlainText('source selection')
    window.editor.set_selection(0, 6)
    if opened:
        window.show_find()
    window.preview.setFocus()
    app.processEvents()
    monkeypatch.setattr(window.preview.page(), 'selectedText', lambda: 'preview selection')
    window.find_action.trigger()
    assert window.find_bar.isVisible()
    assert window.find_action.isChecked()
    assert window.find_input.text() == 'preview selection'


class FeedbackMarkupParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.current_style = ''

    def handle_starttag(self, tag, attrs):
        if tag == 'span':
            self.current_style = dict(attrs).get('style', '')

    def handle_endtag(self, tag):
        if tag == 'span':
            self.current_style = ''

    def handle_data(self, data):
        self.parts.append((data, self.current_style))


def feedback_plain_text(label):
    parser = FeedbackMarkupParser()
    parser.feed(label.text())
    return ''.join(text for text, _ in parser.parts)


@pytest.mark.parametrize('theme', ['light', 'dark', 'soft'])
def test_brand_matches_menu_size_and_keeps_accent(window, app, theme):
    window.set_theme(theme)
    app.processEvents()
    assert window.brand.font().pixelSize() == window.menuBar().font().pixelSize() == 13
    assert window.brand.palette().color(QPalette.WindowText) == QColor(theme_colors(theme)['accent'])
    assert window.brand_icon.width() == window.brand_icon.height() == 20
    pixmap = window.brand_icon.pixmap()
    assert pixmap.width() / pixmap.devicePixelRatio() <= 20


def test_find_text_buttons_share_columns_and_dimensions(window, app):
    window.show_find()
    app.processEvents()
    buttons = (window.find_previous_button, window.find_next_button,
               window.replace_button, window.replace_all_button)
    assert [button.toolTip() for button in buttons] == ['上一个', '下一个', '替换', '全部替换']
    for button in (*buttons, window.find_close_button):
        assert isinstance(button, QToolButton)
        assert button.autoRaise()
        assert button.cursor().shape() == Qt.PointingHandCursor
        assert not button.text()
        assert not button.icon().isNull()
    assert len({button.width() for button in buttons}) == 1
    assert len({button.height() for button in buttons}) == 1
    assert buttons[0].x() == buttons[2].x()
    assert buttons[1].x() == buttons[3].x()
    assert window.regex_checkbox.text() == '.*'
    assert window.case_checkbox.toolTip() == '区分大小写'
    assert window.whole_word_checkbox.toolTip() == '全字匹配'
    assert window.find_bar.width() <= 580
    panel = window.find_bar.parentWidget()
    assert window.find_bar.geometry().right() == panel.contentsRect().right()
    assert not window.regex_checkbox.isChecked()


@pytest.mark.parametrize('mode', ['source', 'preview', 'split'])
def test_find_bar_embeds_at_top_of_visible_pane(window, app, mode):
    window.set_mode(mode)
    window.show_find()
    app.processEvents()
    panel = window.preview_panel if mode == 'preview' else window.source_panel
    assert not window.find_bar.isWindow()
    assert window.find_bar.parentWidget() is panel
    layout = panel.layout()
    anchor = window.preview if mode == 'preview' else window.editor
    assert layout.indexOf(window.find_bar) == -1
    assert window.find_bar.geometry().top() == anchor.geometry().top()
    assert window.find_bar.geometry().right() == anchor.geometry().right()
    before = anchor.geometry()
    window.hide_find()
    app.processEvents()
    assert anchor.geometry() == before
    window.show_find()
    app.processEvents()
    assert anchor.geometry() == before
    for button in (window.case_checkbox, window.whole_word_checkbox, window.regex_checkbox):
        assert button.parentWidget() is window.find_input
        assert window.find_input.rect().contains(button.geometry())
    assert window.find_input.textMargins().right() >= 84
    assert window.find_input.height() == window.replace_input.height() == 32
    for button in window.find_bar.findChildren(QToolButton):
        assert button.cursor().shape() == Qt.PointingHandCursor
    assert window.find_input.width() <= 300
    assert window.replace_input.width() <= 300
    assert window.mode == mode
    assert window.find_bar.isVisible()
    window.find_close_button.click()
    assert window.find_bar.isHidden()
    assert not window.find_action.isChecked()


def test_floating_find_tracks_window_and_splitter_resize(window, app):
    window.set_mode('split')
    window.show_find()
    for width in (1100, 1400):
        window.resize(width, 760)
        app.processEvents()
        window.editor_split.setSizes([width // 3, width * 2 // 3])
        app.processEvents()
        assert window.find_bar.geometry().right() == window.editor.geometry().right()
        assert window.find_bar.geometry().top() == window.editor.geometry().top()
        assert window.source_panel.layout().indexOf(window.find_bar) == -1


def test_find_bar_moves_with_view_and_preserves_query(window, app):
    window.set_mode('source')
    window.show_find()
    window.find_input.setText('cat')
    window.replace_input.setText('dog')
    window.set_mode('preview')
    app.processEvents()
    assert window.find_bar.parentWidget() is window.preview_panel
    assert window.find_bar.isVisible()
    window.editor.setPlainText('cat cat')
    assert window.find_next()
    window.replace_one()
    assert window.mode == 'preview'
    assert window.editor.source_text() == 'dog cat'
    window.set_mode('split')
    assert window.find_bar.parentWidget() is window.source_panel
    assert window.find_input.text() == 'cat'
    assert window.replace_input.text() == 'dog'
    window.hide_find()
    window.set_mode('preview')
    assert window.find_bar.isHidden()


@pytest.mark.parametrize('opened', [False, True])
def test_ctrl_f_uses_source_selection_and_keeps_bar_open(window, app, opened):
    window.set_mode('source')
    window.editor.setPlainText('cat dog')
    if opened:
        window.show_find()
    window.editor.set_selection(4, 7)
    window.editor.setFocus()
    QTest.keyClick(window.editor, Qt.Key_F, Qt.ControlModifier)
    app.processEvents()
    assert window.find_bar.isVisible()
    assert window.find_action.isChecked()
    assert window.find_input.text() == 'dog'


def test_ctrl_f_without_source_selection_toggles_bar(window, app):
    window.set_mode('source')
    window.editor.setPlainText('cat dog')
    window.editor.setFocus()
    QTest.keyClick(window.editor, Qt.Key_F, Qt.ControlModifier)
    app.processEvents()
    assert window.find_bar.isVisible()
    window.find_input.setText('cat')
    window.find_input.selectAll()
    QTest.keyClick(window.find_input, Qt.Key_F, Qt.ControlModifier)
    app.processEvents()
    assert window.find_bar.isHidden()
    assert not window.find_action.isChecked()
    QTest.keyClick(window.editor, Qt.Key_F, Qt.ControlModifier)
    app.processEvents()
    assert window.find_bar.isVisible()


@pytest.mark.parametrize(('case', 'whole', 'regex', 'expected'), [
    (False, False, False, 'x x sx'),
    (True, False, False, 'Cat x sx'),
    (False, True, False, 'x x scat'),
    (True, True, False, 'Cat x scat'),
    (True, True, True, 'Cat x scat'),
])
def test_search_options_apply_to_count_find_and_replace(window, case, whole, regex, expected):
    window.editor.setPlainText('Cat cat scat')
    window.find_input.setText('c.t' if regex else 'cat')
    window.replace_input.setText('x')
    window.case_checkbox.setChecked(case)
    window.whole_word_checkbox.setChecked(whole)
    window.regex_checkbox.setChecked(regex)
    assert window._find_match_count(window.find_input.text()) == expected.count('x')
    assert window.find_next()
    assert window.editor.selected_text() == ('cat' if case else 'Cat')
    window.replace_all()
    assert window.editor.source_text() == expected
    window.editor.undo()
    assert window.editor.source_text() == 'Cat cat scat'


def test_whole_word_replace_one_skips_partial_selection(window):
    window.editor.setPlainText('scat cat Cat')
    window.editor.set_selection(1, 4)
    window.find_input.setText('cat')
    window.replace_input.setText('dog')
    window.whole_word_checkbox.setChecked(True)
    window.case_checkbox.setChecked(True)
    window.replace_one()
    assert window.editor.source_text() == 'scat dog Cat'


def test_find_feedback_shows_document_match_count(window):
    window.editor.setPlainText('cat Cat scatter')
    window.find_input.setText('cat')
    assert window.find_next()
    assert '3' in window.find_feedback.text()


@pytest.mark.parametrize(('theme', 'green', 'red'), [
    ('light', '#188038', '#c5221f'),
    ('soft', '#188038', '#c5221f'),
    ('dark', '#81c995', '#f28b82'),
    ('nv', '#81c995', '#f28b82'),
])
def test_find_feedback_colors_numbers_and_no_match_message(window, theme, green, red):
    window.set_theme(theme)
    window.editor.setPlainText('cat')
    window.find_input.setText('cat')
    assert window.find_next()
    parser = FeedbackMarkupParser()
    parser.feed(window.find_feedback.text())
    assert any(text == '1' and f'color:{green}' in style for text, style in parser.parts)

    window.find_input.setText('dog')
    assert not window.find_next()
    parser = FeedbackMarkupParser()
    parser.feed(window.find_feedback.text())
    assert any(text == '未找到匹配内容' and f'color:{red}' in style
               for text, style in parser.parts)
    assert any(text == '0' and f'color:{green}' in style for text, style in parser.parts)

    window.editor.setPlainText('x x')
    window.find_input.setText('x')
    window.replace_input.setText('y')
    window.replace_all()
    parser = FeedbackMarkupParser()
    parser.feed(window.find_feedback.text())
    assert any(text == '2' and f'color:{green}' in style for text, style in parser.parts)
    assert any(text == '0' and f'color:{green}' in style for text, style in parser.parts)


def test_find_feedback_escapes_dynamic_text_before_rich_text_rendering(window):
    assert window.find_feedback.textFormat() == Qt.RichText
    window._find_message('<b>failure</b> 2')
    parser = FeedbackMarkupParser()
    parser.feed(window.find_feedback.text())
    assert ''.join(text for text, _ in parser.parts) == '<b>failure</b> 2'
    assert '<b>' not in window.find_feedback.text()
    assert any(text == '2' and 'color:' in style for text, style in parser.parts)


def test_find_feedback_font_is_larger(window):
    assert window.find_feedback.font().pixelSize() == 14


def test_find_feedback_shows_zero_matches(window):
    window.editor.setPlainText('cat')
    window.find_input.setText('dog')
    assert not window.find_next()
    assert '0' in window.find_feedback.text()


def test_regex_find_feedback_counts_entire_document(window):
    window.editor.setPlainText('item=12 item=34')
    window.editor.set_selection(8, 8)
    window.find_input.setText(r'item=\d+')
    window.regex_checkbox.setChecked(True)
    assert window.find_next()
    assert '2' in window.find_feedback.text()


def test_replace_all_feedback_shows_remaining_matches(window):
    window.editor.setPlainText('x x')
    window.find_input.setText('x')
    window.replace_input.setText('y')
    window.replace_all()
    assert window.editor.source_text() == 'y y'
    assert '2' in window.find_feedback.text()
    assert '0' in window.find_feedback.text()


def test_replace_one_feedback_shows_zero_remaining_matches(window):
    window.editor.setPlainText('cat')
    window.find_input.setText('cat')
    window.replace_input.setText('dog')
    window.replace_one()
    assert window.editor.source_text() == 'dog'
    message = feedback_plain_text(window.find_feedback)
    assert '已替换 1 处' in message
    assert '当前文档剩余匹配 0 处' in message


@pytest.mark.parametrize('theme', ['light', 'dark', 'soft'])
def test_regex_checkbox_indicator_is_visible_and_clickable(window, app, theme):
    window.set_theme(theme)
    window.show_find()
    app.processEvents()
    checkbox = window.regex_checkbox
    assert checkbox.isVisible()
    assert checkbox.width() > 0
    assert checkbox.height() >= 20
    assert 'QCheckBox::indicator' in window.styleSheet()
    QTest.mouseClick(checkbox, Qt.LeftButton, pos=QPoint(8, checkbox.height() // 2))
    assert checkbox.isChecked()


def test_regex_dialog_search_replace_and_undo(window):
    window.editor.setPlainText('中文 item=12\nitem=34')
    window.editor.set_selection(0, 0)
    window.find_input.setText(r'item=(\d+)')
    window.regex_checkbox.setChecked(True)
    assert window.find_next()
    assert window.editor.selected_text() == 'item=12'
    assert window.find_next(backward=True)
    assert window.editor.selected_text() == 'item=34'
    window.replace_input.setText(r'编号=\1')
    window.replace_all()
    assert window.editor.source_text() == '中文 编号=12\n编号=34'
    assert '2' in window.find_feedback.text()
    window.editor.undo()
    assert window.editor.source_text() == '中文 item=12\nitem=34'


def test_regex_error_stays_in_dialog_and_preserves_document(window):
    window.editor.setPlainText('保留内容')
    window.regex_checkbox.setChecked(True)
    window.find_input.setText('[')
    assert not window.find_next()
    assert '正则' in window.find_feedback.text()
    window.replace_all()
    assert window.editor.source_text() == '保留内容'
    window.find_input.setText('不存在')
    assert not window.find_next()
    assert '未找到匹配内容' in window.find_feedback.text()
    assert '0' in window.find_feedback.text()


def test_regex_is_optional_and_literal_replacement_preserves_backslash(window):
    window.editor.setPlainText('a.b A.B axb')
    window.find_input.setText('a.b')
    window.replace_input.setText(r'\1')
    window.replace_all()
    assert window.editor.source_text() == r'\1 \1 axb'


def test_replace_button_uses_regex_capture_and_finds_next(window):
    window.editor.setPlainText('编号12 编号34')
    window.editor.set_selection(0, 0)
    window.regex_checkbox.setChecked(True)
    window.find_input.setText(r'编号(\d+)')
    window.replace_input.setText(r'项目\1')
    window.replace_one()
    assert window.editor.source_text() == '项目12 编号34'
    assert window.editor.selected_text() == '编号34'
    message = feedback_plain_text(window.find_feedback)
    assert '已替换 1 处' in message
    assert '当前文档剩余匹配 1 处' in message

