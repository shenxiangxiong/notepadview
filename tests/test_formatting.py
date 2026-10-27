import json
import pytest
from notepadview.formatting import format_json, format_xml, format_sql


def test_json_preserves_unicode_and_key_order():
    result = format_json('{"z":"中文","a":[1,2]}')
    assert result == '{\n  "z": "中文",\n  "a": [\n    1,\n    2\n  ]\n}'


def test_xml_keeps_declaration_comments_and_processing_instruction():
    result = format_xml('<?xml version="1.0"?><?view keep?><root><!--keep--><item>中文</item></root>')
    assert result.startswith('<?xml')
    assert '<?view keep?>' in result and '<!--keep-->' in result
    assert '\n  <item>中文</item>' in result


def test_xml_preserves_mixed_content():
    assert '<p>hello <b>world</b> !</p>' in format_xml('<p>hello <b>world</b> !</p>')


def test_sql_only_uppercases_keywords():
    result = format_sql("select userName, 'select' from Users -- keep select\nwhere id=1")
    assert "SELECT userName," in result and "'select'" in result
    assert 'FROM Users' in result and '-- keep select' in result
    assert 'WHERE id = 1' in result


@pytest.mark.parametrize('formatter,source', [(format_json, '{oops'), (format_xml, '<root>')])
def test_invalid_input_raises(formatter, source):
    with pytest.raises(ValueError):
        formatter(source)


def test_json_preserves_exact_numeric_literals_and_duplicate_keys():
    source = '{"n":0.12345678901234567890123456789,"huge":1e400,"id":1,"id":2}'
    result = format_json(source)
    assert '0.12345678901234567890123456789' in result
    assert '1e400' in result and 'Infinity' not in result
    assert result.count('"id"') == 2


@pytest.mark.parametrize('source', ['{"n":NaN}', '{"n":Infinity}'])
def test_json_rejects_nonstandard_constants(source):
    with pytest.raises(ValueError):
        format_json(source)


def test_xml_preserves_whitespace_leaf_value():
    assert '<value> </value>' in format_xml('<root><value> </value></root>')


def test_sql_preserves_adjacent_string_literal_newline():
    result = format_sql("select 'a'\n'b';")
    assert "'a'\n'b'" in result
    assert result.startswith('SELECT')


def test_json_parse_error_is_chinese_and_keeps_decode_error_contract():
    source = '{\n  "a":\n}'
    with pytest.raises(json.JSONDecodeError) as caught:
        format_json(source)
    error = caught.value
    assert 'JSON' in str(error) and '缺少值' in str(error)
    assert '第 3 行' in str(error) and '第 1 列' in str(error)
    assert 'Expecting' not in str(error) and 'line' not in str(error)
    assert (error.doc, error.pos, error.lineno, error.colno) == (source, 9, 3, 1)
    assert isinstance(error.__cause__, json.JSONDecodeError)
    assert error.__cause__.msg == 'Expecting value'


@pytest.mark.parametrize('source', ['{"n":NaN}', '{"n":Infinity}', '{"n":-Infinity}'])
def test_json_constant_error_has_chinese_reason(source):
    with pytest.raises(ValueError, match='JSON.*常量') as caught:
        format_json(source)
    assert 'Invalid' not in str(caught.value)


def test_xml_parse_error_keeps_value_error_and_original_cause():
    from xml.parsers.expat import ExpatError
    with pytest.raises(ValueError) as caught:
        format_xml('<root>\n</wrong>')
    error = caught.value
    message = str(error)
    assert 'XML' in message and '不匹配' in message
    assert '第 2 行' in message
    assert f'第 {error.__cause__.offset} 列' in message
    assert 'mismatched tag' not in message and 'line' not in message
    assert isinstance(error.__cause__, ExpatError)


@pytest.mark.parametrize('source', [
    '<!DOCTYPE root><root/>', '<!ENTITY test "value"><root/>',
])
def test_xml_unsupported_declaration_error_is_chinese(source):
    with pytest.raises(ValueError, match='XML.*不支持.*实体'):
        format_xml(source)


def test_sql_complexity_error_is_chinese_value_error():
    from sqlparse.exceptions import SQLParseError
    source = 'select ' + '(' * 110 + '1' + ')' * 110
    with pytest.raises(ValueError, match='SQL.*嵌套') as caught:
        format_sql(source)
    assert isinstance(caught.value, SQLParseError)
    assert isinstance(caught.value.__cause__, SQLParseError)
    assert 'Maximum' not in str(caught.value)


@pytest.mark.parametrize('formatter,source', [
    (format_json, '"\\uZZZZ"'),
    (format_json, '{"n":NaN}'),
    (format_xml, '<root><![CDATA[unfinished'),
    (format_xml, '<root>\n</wrong>'),
    (format_sql, 'select ' + '(' * 110 + '1' + ')' * 110),
])
def test_formatting_errors_survive_ui_error_conversion(formatter, source):
    from notepadview.errors import chinese_error
    with pytest.raises(ValueError) as caught:
        formatter(source)
    assert chinese_error(caught.value) == str(caught.value)
    assert chinese_error(ValueError(chinese_error(caught.value))) == str(caught.value)
