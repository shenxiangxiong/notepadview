import json

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from notepadview import formatting as fmt
from notepadview.editor import SourceEditor


@pytest.fixture
def editor():
    app = QApplication.instance() or QApplication([])
    widget = SourceEditor()
    yield widget
    widget.close()
    app.processEvents()


def test_compact_json_preserves_numbers_duplicates_and_strings():
    source = '{ "数": 1e400, "数": 0.123456789012345678901, "s": "a b" }'
    assert fmt.compact_json(source) == '{"数":1e400,"数":0.123456789012345678901,"s":"a b"}'


@pytest.mark.parametrize('source,expected', [
    ('{\'中文\': 1, // 注释\n}', '{"中文": 1}'),
    ('{"url":"https://a/*b*/",}', '{"url": "https://a/*b*/"}'),
    (r'{/* \"注释\" */"a":1,}', '{"a":1}'),
    ('{// \\"注释\\"\n"a":1,}', '{"a":1}'),
])
def test_conservative_json_repair(source, expected):
    assert json.loads(fmt.repair_json(source)) == json.loads(expected)


@pytest.mark.parametrize('source', ['{"a":', '{"a":1', '{"a":unknown}', '{"a":1 "b":}', '{"a":"unfinished}'])
def test_json_repair_never_invents_data(source):
    with pytest.raises(ValueError):
        fmt.repair_json(source)


ESCAPED_KEY_LOG = r'{\"时间\":"2026-10-23T18:10:08.9070138+08:00","请求编号":"abc","详情":"{\"合同编号\":1048251,\"模块编码\":\"Demo.Module\",\"来源\":\"合同文件\"}","异常":null}'


def test_json_repair_removes_only_escaped_key_delimiters():
    expected = ESCAPED_KEY_LOG.replace(r'\"时间\"', '"时间"', 1)
    assert fmt.repair_json(ESCAPED_KEY_LOG) == fmt.format_json(expected)
    assert isinstance(json.loads(fmt.repair_json(ESCAPED_KEY_LOG))['详情'], str)


@pytest.mark.parametrize('whole_layer', [False, True])
def test_json_repair_preserves_escapes_numbers_and_duplicate_order(whole_layer):
    valid = r'{"时间":"ok","详情":"{\"合同编号\":1048251}","路径":"C:\\new\\目录","转义":"\u4e2d\n\\n","数":0.123456789012345678901,"数":1e400}'
    damaged = json.dumps(valid, ensure_ascii=False)[1:-1] if whole_layer else valid.replace('"时间"', r'\"时间\"', 1)
    assert fmt.repair_json(damaged) == fmt.format_json(valid)
    assert fmt.repair_json(valid) == fmt.format_json(valid)


@pytest.mark.parametrize('valid', [r'{"a":"b"}', r'["a",{"a":"{\"b\":1}"}]'])
def test_json_repair_decodes_one_complete_escape_layer(valid):
    escaped = json.dumps(valid, ensure_ascii=False)[1:-1]
    assert fmt.repair_json(escaped) == fmt.format_json(valid)


@pytest.mark.parametrize('source', [
    r'{\"时间\":', r'{\"时间\":1', r'{\"时间":1}', r'{"时间\":1}',
    r'{\"时间\":unknown}', r'{\"时间\":\"unfinished}',
    r'{\"时间\":1,}', r'{\"时间\":1} garbage',
])
def test_json_repair_refuses_incomplete_or_mixed_escape_repairs(source):
    with pytest.raises(ValueError):
        fmt.repair_json(source)


@pytest.mark.parametrize('layers', [2, 3])
@pytest.mark.parametrize('extra_closing', [False, True])
def test_json_repair_decodes_multiple_layers_and_redundant_object_closing(layers, extra_closing):
    valid = r'{"contract_id":"1048301","contract_type":"施工合同","files":[{"file_name":"合同模板.docx","file_url":"https://example.test/%E6%AD%A3.docx","file_type":0}],"extra_fields":["合同名称","签订日期"],"path":"C:\\new\\合同","detail":"{\"id\":1}","n":1e400,"n":0.123456789012345678901}'
    source = valid + ('}' if extra_closing else '')
    for _ in range(layers):
        source = json.dumps(source, ensure_ascii=False)[1:-1]
    assert fmt.repair_json(source) == fmt.format_json(valid)


@pytest.mark.parametrize('tail', [' garbage', '}{"other":1}', ']', '}}', ','])
def test_multilayer_repair_does_not_discard_other_trailing_content(tail):
    source = '{"id":1}' + tail
    for _ in range(2):
        source = json.dumps(source)[1:-1]
    with pytest.raises(ValueError):
        fmt.repair_json(source)


def test_escaped_key_repair_uses_preview_cancel_apply_and_undo(editor, monkeypatch):
    from notepadview.format_preview import RepairPreviewDialog
    editor.set_language('json')
    editor.setPlainText(ESCAPED_KEY_LOG)
    def cancel(dialog):
        assert dialog.before.toPlainText() == ESCAPED_KEY_LOG
        assert json.loads(dialog.after.toPlainText())['请求编号'] == 'abc'
        assert dialog.diff.toPlainText()
        return QDialog.Rejected
    monkeypatch.setattr(RepairPreviewDialog, 'exec', cancel)
    assert not editor.apply_format_operation('repair')
    assert editor.source_text() == ESCAPED_KEY_LOG
    monkeypatch.setattr(RepairPreviewDialog, 'exec', lambda self: QDialog.Accepted)
    assert editor.apply_format_operation('repair')
    editor.undo()
    assert editor.source_text() == ESCAPED_KEY_LOG


def test_json_repair_merges_chinese_log_records_without_changing_tokens():
    first = r'{"消息":"完成","空":null,"详情":"{\"名称\":\"中文\",\"路径\":\"C:\\\\日志\"}","数":0.12345678901234567890123456789,"数":1e400,"转义":"\u4e2d"}'
    second = r'{"消息":"等待","详情":"{\"结果\":null}","数":-0.000,"数":2E+30}'
    source = first + '\n' + second
    with pytest.raises(json.JSONDecodeError) as caught:
        fmt.format_json(source)
    assert caught.value.msg == 'Extra data'
    repaired = fmt.repair_json(source)
    assert first in repaired and second in repaired
    records = json.loads(repaired, parse_float=str, object_pairs_hook=list)
    assert len(records) == 2
    assert records[0][2] == ('详情', json.loads(first)['详情'])
    assert isinstance(records[0][2][1], str)
    assert records[0][3:5] == [('数', '0.12345678901234567890123456789'), ('数', '1e400')]


@pytest.mark.parametrize('separator', ['\n', '\r\n', '\r', ' ', '\t', ' \n\t'])
def test_json_repair_merges_only_whitespace_separated_containers(separator):
    source = ' \t{"中文":null}' + separator + '[{"详情":"[]"},[]]' + separator + '{}\r\n'
    assert json.loads(fmt.repair_json(source)) == [{'中文': None}, [{'详情': '[]'}, []], {}]


@pytest.mark.parametrize('source', [
    '{}\n{} garbage', '{}\n{"中文":', '{}\n{}\n[', '{}{}', '{}\n{}[]',
    '{}\n1', 'null\n{}', '[]\n"中文"', '{}\v{}', '{}\u00a0{}',
    '{}\n{"n":NaN}', '{}\n{"n":Infinity}', '{}\n{"n":-Infinity}',
    '{}\n{"a":1,}', '{}/* 注释 */{}', "{}\n{'a':1}",
])
def test_json_record_repair_refuses_invalid_or_ambiguous_streams(source):
    with pytest.raises(ValueError):
        fmt.repair_json(source)


@pytest.mark.parametrize('source', ['{"中文":null}', '[{"a":1}]', '1e400', '"中文"', 'null'])
def test_json_repair_keeps_single_value_behavior(source):
    assert fmt.repair_json(source) == fmt.format_json(source)


@pytest.mark.parametrize('selected', [False, True])
def test_json_records_use_preview_confirmation_and_one_undo(editor, monkeypatch, selected):
    from notepadview.format_preview import RepairPreviewDialog
    editor.set_language('json')
    records = '{"消息":"完成","详情":"{\\"结果\\":null}"}\r\n{"消息":"等待","空":null}'
    original = '前文 ' + records + ' 后文' if selected else records
    editor.setPlainText(original)
    if selected:
        editor.set_selection(3, 3 + len(records))
    seen = []
    def cancel(dialog):
        seen.append((dialog.before.toPlainText(), dialog.after.toPlainText(), dialog.diff.toPlainText()))
        assert editor.source_text() == original
        return QDialog.Rejected
    monkeypatch.setattr(RepairPreviewDialog, 'exec', cancel)
    assert not editor.apply_format_operation('repair')
    assert editor.source_text() == original
    assert seen[0][0].replace('\n', '\r\n') == records
    assert json.loads(seen[0][1]) == [json.loads(line) for line in records.splitlines()]
    assert seen[0][1] == fmt.format_json('[' + ','.join(records.splitlines()) + ']')
    assert '-{"消息"' in seen[0][2] and '+[' in seen[0][2]
    monkeypatch.setattr(RepairPreviewDialog, 'exec', lambda self: QDialog.Accepted)
    assert editor.apply_format_operation('repair')
    result = editor.source_text()[3:-3] if selected else editor.source_text()
    assert '\r\n' in result and '\n' not in result.replace('\r\n', '')
    assert json.loads(result) == [json.loads(line) for line in records.splitlines()]
    editor.undo()
    assert editor.source_text() == original


@pytest.mark.parametrize('source', ['{}\n{} garbage', '{}\n{"中文":', '{}{}', r'{\"时间\":', r'{\"时间":1}'])
def test_invalid_json_records_keep_editor_text_without_preview(editor, monkeypatch, source):
    from notepadview.format_preview import RepairPreviewDialog
    editor.set_language('json')
    editor.setPlainText(source)
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[2]))
    def unexpected_preview(dialog):
        pytest.fail('无效记录不应进入预览')
    monkeypatch.setattr(RepairPreviewDialog, 'exec', unexpected_preview)
    assert not editor.apply_format_operation('repair')
    assert editor.source_text() == source
    assert not editor.document().isModified()
    assert warnings


def test_xml_repairs_bare_ampersand_and_unquoted_attribute():
    assert fmt.repair_xml('<r id=123>中文 & text &amp; more</r>') == '<r id="123">中文 &amp; text &amp; more</r>'


@pytest.mark.parametrize('source', ['<r>', '<r></wrong>', '<r>&unknown;</r>', '<!DOCTYPE r><r/>'])
def test_xml_repair_refuses_incomplete_or_ambiguous_data(source):
    with pytest.raises(ValueError):
        fmt.repair_xml(source)


def test_menu_only_has_current_language_and_readonly_state(editor):
    editor.setPlainText('中文')
    editor.set_language('json')
    labels = [a.text() for a in editor.build_context_menu().actions()]
    assert 'JSON 格式化' in labels and 'JSON 自动修复…' in labels
    assert '一级标题' not in labels and 'XML 格式化' not in labels
    editor.setReadOnly(True)
    entries = {a.text(): a for a in editor.build_context_menu().actions()}
    assert not entries['JSON 自动修复…'].isEnabled()
    assert entries['JSON 语法检查'].isEnabled()


@pytest.mark.parametrize('operation,expected', [('heading3', '### 中文'), ('paragraph', '中文'), ('bold', '**中文**'), ('italic', '*中文*'), ('list', '- 中文'), ('quote', '> 中文'), ('code', '```\n中文\n```')])
def test_markdown_current_line_and_single_undo(editor, operation, expected):
    editor.setPlainText('前文\r\n中文\r\n后文')
    editor.set_selection(5, 5)
    assert editor.apply_format_operation(operation)
    assert editor.source_text() == '前文\r\n' + expected.replace('\n', '\r\n') + '\r\n后文'
    if operation != 'paragraph':
        editor.undo()
        assert editor.source_text() == '前文\r\n中文\r\n后文'


def test_selection_precedes_whole_document_and_undo(editor):
    editor.set_language('json')
    editor.setPlainText('前缀 {"中文":1} 后缀')
    editor.set_selection(3, 11)
    assert editor.apply_format_operation('format')
    assert editor.source_text() == '前缀 {\n  "中文": 1\n} 后缀'
    editor.undo()
    assert editor.source_text() == '前缀 {"中文":1} 后缀'


def test_error_position_uses_unicode_and_selection_offset(editor, monkeypatch):
    editor.set_language('json')
    editor.setPlainText('前文\n  {"中文": }\n后文')
    editor.set_selection(5, 13)
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[2]))
    original = editor.source_text()
    assert not editor.apply_format_operation('check')
    assert editor.source_text() == original
    assert editor.selection_range() == (12, 12)
    assert warnings and '第 2 行' in warnings[0]


def test_repair_preview_cancel_accept_and_one_undo(editor, monkeypatch):
    from notepadview.format_preview import RepairPreviewDialog
    editor.set_language('json')
    original = '{"中文":1,}'
    editor.setPlainText(original)
    seen = []
    def cancel(dialog):
        seen.append((dialog.before.toPlainText(), dialog.after.toPlainText(), dialog.diff.toPlainText()))
        return QDialog.Rejected
    monkeypatch.setattr(RepairPreviewDialog, 'exec', cancel)
    assert not editor.apply_format_operation('repair')
    assert editor.source_text() == original
    assert seen[0][0] == original and '-{"中文":1,}' in seen[0][2]
    monkeypatch.setattr(RepairPreviewDialog, 'exec', lambda self: QDialog.Accepted)
    assert editor.apply_format_operation('repair')
    assert json.loads(editor.source_text()) == {'中文': 1}
    editor.undo()
    assert editor.source_text() == original


@pytest.mark.parametrize('operation,source,expected', [
    ('trim', '中文  \r\n尾\t', '中文\r\n尾'),
    ('blank', '中文\r\n \r\n\r\n尾\r\n', '中文\r\n尾\r\n'),
])
def test_text_cleanup(editor, operation, source, expected):
    editor.set_language('txt')
    editor.setPlainText(source)
    assert editor.apply_format_operation(operation)
    assert editor.source_text() == expected
    editor.undo()
    assert editor.source_text() == source


def test_sql_case_preserves_literals_comments_identifiers(editor):
    editor.set_language('sql')
    editor.setPlainText("SELECT 中文, 'SELECT' FROM Users -- SELECT\nWHERE id=1")
    assert editor.apply_format_operation('lower')
    assert editor.source_text() == "select 中文, 'SELECT' from Users -- SELECT\nwhere id=1"


def test_xml_repair_does_not_guess_slash_at_end_of_unquoted_value():
    with pytest.raises(ValueError):
        fmt.repair_xml('<r url=https://example/path/>')


def test_json_repair_rejects_python_only_escapes():
    with pytest.raises(ValueError):
        fmt.repair_json(r"{'value': '\x41'}")


def test_repair_does_not_overwrite_edits_during_preview(editor, monkeypatch):
    from notepadview.format_preview import RepairPreviewDialog
    editor.set_language('json')
    editor.setPlainText('{"a":1,}')
    def changed(dialog):
        editor.setPlainText('new content')
        return QDialog.Accepted
    monkeypatch.setattr(RepairPreviewDialog, 'exec', changed)
    assert not editor.apply_format_operation('repair')
    assert editor.source_text() == 'new content'


def test_json_repair_preserves_data_after_cr_comment():
    assert json.loads(fmt.repair_json('[1,// comment\r2\n]')) == [1, 2]


def test_window_format_command_prefers_selection(editor):
    from types import SimpleNamespace
    from notepadview.window import MainWindow
    editor.set_language('json')
    editor.setPlainText('[{"a":1},{"b":2}]')
    editor.set_selection(1, 8)
    window = SimpleNamespace(editor=editor, new_document_format='json', is_readonly_preview=False,
                             render_preview=lambda: None)
    assert MainWindow.format_document(window)
    assert editor.source_text() == '[{\n  "a": 1\n},{"b":2}]'


def test_window_format_command_honors_readonly():
    from types import SimpleNamespace
    from notepadview.window import MainWindow
    app = QApplication.instance() or QApplication([])
    editor = SourceEditor()
    editor.set_language('json')
    editor.setPlainText('{"a":1}')
    editor.setReadOnly(True)
    window = SimpleNamespace(editor=editor, new_document_format='json', is_readonly_preview=False,
                             render_preview=lambda: None)
    assert not MainWindow.format_document(window)
    assert editor.source_text() == '{"a":1}'
    editor.close()
    app.processEvents()


@pytest.mark.parametrize('level', range(1, 7))
def test_all_heading_levels_replace_existing_heading(editor, level):
    editor.setPlainText('## 中文')
    assert editor.apply_format_operation('heading' + str(level))
    assert editor.source_text() == '#' * level + ' 中文'


@pytest.mark.parametrize('language,source,operation', [
    ('json', '{"中文":1}', 'check'), ('xml', '<r>中文</r>', 'check'),
    ('json', '{"中文":1}', 'repair'), ('xml', '<r>中文</r>', 'repair'),
])
def test_valid_structure_check_or_repair_does_not_modify(editor, monkeypatch, language, source, operation):
    editor.set_language(language)
    editor.setPlainText(source)
    messages = []
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: messages.append(args[2]))
    assert editor.apply_format_operation(operation)
    assert editor.source_text() == source
    assert not editor.document().isModified()
    assert messages


def test_xml_repair_preview_selection_and_error_location(editor, monkeypatch):
    from notepadview.format_preview import RepairPreviewDialog
    editor.set_language('xml')
    original = '前文 <r>中文 & 内容</r> 后文'
    editor.setPlainText(original)
    editor.set_selection(3, len(original) - 3)
    monkeypatch.setattr(RepairPreviewDialog, 'exec', lambda self: QDialog.Accepted)
    assert editor.apply_format_operation('repair')
    assert editor.source_text() == '前文 <r>中文 &amp; 内容</r> 后文'
    editor.undo()
    assert editor.source_text() == original
    editor.setPlainText('中文\n<r>\n</wrong>')
    editor.set_selection(3, len(editor.source_text()))
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: None)
    assert not editor.apply_format_operation('check')
    assert editor.cursor_position() == (3, 3)


def test_preview_diff_has_separate_removed_and_added_lines(editor):
    from notepadview.format_preview import RepairPreviewDialog
    dialog = RepairPreviewDialog('{"a":1,}', '{"a":1}', editor)
    assert '-{"a":1,}\n+{"a":1}' in dialog.diff.toPlainText()
    dialog.close()


def test_readonly_and_multiple_selections_cannot_modify(editor):
    editor.setPlainText('中文')
    editor.setReadOnly(True)
    assert not editor.apply_format_operation('heading1')
    editor.setReadOnly(False)
    editor.addSelection(4, 4)
    assert not editor.apply_format_operation('heading1')
    assert editor.source_text() == '中文'
