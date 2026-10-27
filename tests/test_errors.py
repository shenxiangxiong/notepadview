import errno
import json
from xml.parsers import expat

import pytest
from sqlparse.exceptions import SQLParseError


def chinese_error(error):
    from notepadview.errors import chinese_error as convert
    return convert(error)


@pytest.mark.parametrize('error,reason', [
    (json.JSONDecodeError('Expecting value', '\n?', 1), '缺少值'),
    (OSError(errno.EACCES, 'Permission denied', 'C:/data/file'), '权限'),
    (UnicodeError('invalid encoding'), '编码'),
    (ValueError('Invalid JSON constant: NaN'), '常量'),
    (ValueError('Unsupported workspace schema version 5; supported version is 1'), '版本'),
    (ValueError('Cannot recover corrupt workspace cache C:/cache.json: secret'), '缓存'),
    (Exception('secret'), '未知'),
])
def test_chinese_error_converts_known_and_unknown_errors(error, reason):
    message = chinese_error(error)
    assert reason in message
    assert 'secret' not in message and 'Permission denied' not in message


def test_existing_chinese_value_error_is_preserved():
    message = 'JSON 格式错误：第 2 行，第 3 列缺少值。'
    assert chinese_error(ValueError(message)) == message


def test_xml_error_has_chinese_location_and_reason():
    with pytest.raises(expat.ExpatError) as caught:
        expat.ParserCreate().Parse('<root>\n</wrong>', True)
    message = chinese_error(caught.value)
    assert 'XML' in message and '不匹配' in message
    assert f'第 {caught.value.lineno} 行' in message
    assert f'第 {caught.value.offset} 列' in message


def test_json_error_has_chinese_location():
    message = chinese_error(json.JSONDecodeError('Expecting value', '\n?', 1))
    assert 'JSON' in message and '第 2 行' in message and '第 1 列' in message


def test_os_error_preserves_path():
    path = r'C:\资料\original file.JSON'
    assert path in chinese_error(OSError(errno.EACCES, 'Permission denied', path))


@pytest.mark.parametrize('source,reason', [
    ('{oops', '双引号'), ('{"a":}', '缺少值'),
    ('{"a" 1}', '冒号'), ('{"a":1 "b":2}', '逗号'),
    ('[1 2]', '逗号'), ('"unfinished', '字符串未结束'),
    ('"\\q"', '转义'), ('"\\uZZZZ"', '转义'),
    ('"a\nb"', '控制字符'), ('{} {}', '多余内容'),
    ('\ufeff{}', '字节顺序标记'),
])
def test_json_error_has_chinese_reason_and_location(source, reason):
    with pytest.raises(json.JSONDecodeError) as caught:
        json.loads(source)
    error = caught.value
    message = chinese_error(error)
    assert 'JSON' in message and reason in message
    assert f'第 {error.lineno} 行' in message
    assert f'第 {error.colno} 列' in message
    assert error.msg not in message


def test_unrecognized_json_reason_does_not_leak_english():
    message = chinese_error(json.JSONDecodeError('new parser diagnostic', '\n?', 1))
    assert 'JSON' in message and '第 2 行' in message and '第 1 列' in message
    assert 'new parser diagnostic' not in message


@pytest.mark.parametrize('source,reason', [
    ('<root>\n</wrong>', '不匹配'), ('<root>', '未闭合'),
    ('<root>&missing;</root>', '实体'),
    ('<root a="1" a="2"/>', '重复属性'),
    ('<root>\x00</root>', '无效字符'),
    ('<root/><extra/>', '多余内容'),
])
def test_xml_error_has_chinese_reason_and_parser_location(source, reason):
    with pytest.raises(expat.ExpatError) as caught:
        expat.ParserCreate().Parse(source, True)
    error = caught.value
    message = chinese_error(error)
    assert 'XML' in message and reason in message
    assert f'第 {error.lineno} 行' in message
    assert f'第 {error.offset} 列' in message
    assert 'line' not in message and 'column' not in message


@pytest.mark.parametrize('code,reason', [
    (errno.ENOENT, '不存在'), (errno.EACCES, '权限'),
    (errno.EPERM, '权限'), (errno.EEXIST, '已存在'),
    (errno.ENOSPC, '空间不足'), (errno.ENOTDIR, '不是目录'),
    (errno.EISDIR, '是目录'), (errno.EROFS, '只读'),
    (errno.EBUSY, '占用'), (errno.ENOTEMPTY, '非空'),
    (errno.ENAMETOOLONG, '过长'), (errno.EMFILE, '打开的文件过多'),
    (errno.EIO, '读写'),
])
def test_os_error_uses_code_not_system_message_and_preserves_path(code, reason):
    path = r'C:\资料\original file.JSON'
    message = chinese_error(OSError(code, 'untranslated system diagnostic', path))
    assert reason in message and path in message
    assert 'untranslated system diagnostic' not in message


@pytest.mark.parametrize('code,reason', [
    (2, '不存在'), (3, '不存在'), (5, '权限'), (32, '占用'),
    (33, '锁定'), (80, '已存在'), (112, '空间不足'),
    (183, '已存在'), (206, '过长'), (267, '目录'),
])
def test_winerror_takes_precedence_over_errno(code, reason):
    error = OSError(errno.EIO, 'English Windows error', r'C:\data\file.txt')
    error.winerror = code
    assert reason in chinese_error(error)
    assert error.filename in chinese_error(error)
    assert 'English Windows error' not in chinese_error(error)


def test_os_error_includes_both_paths_without_rewriting_them():
    error = OSError(errno.EACCES, 'Permission denied', '/data/原文件', None, '/data/new file')
    message = chinese_error(error)
    assert error.filename in message and error.filename2 in message


@pytest.mark.parametrize('error,reason', [
    (FileNotFoundError('secret'), '不存在'),
    (PermissionError('secret'), '权限'), (FileExistsError('secret'), '已存在'),
    (IsADirectoryError('secret'), '是目录'),
    (NotADirectoryError('secret'), '不是目录'), (TimeoutError('secret'), '超时'),
    (OSError(98765, 'secret'), '文件系统'), (OSError('secret'), '文件系统'),
])
def test_os_error_subtypes_and_unknown_codes(error, reason):
    message = chinese_error(error)
    assert reason in message and 'secret' not in message


@pytest.mark.parametrize('error,reason', [
    (UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'invalid start byte'), '解码'),
    (UnicodeEncodeError('ascii', '中', 0, 1, 'ordinal not in range'), '编码'),
    (UnicodeTranslateError('中', 0, 1, 'undefined mapping'), '转换'),
    (UnicodeError('secret encoding diagnostic'), '编码'),
])
def test_unicode_errors_have_chinese_explanation(error, reason):
    message = chinese_error(error)
    assert reason in message and '字符' in message
    assert 'invalid' not in message and 'secret' not in message
    assert 'ordinal' not in message and 'undefined' not in message


@pytest.mark.parametrize('message,reason', [
    ('Invalid JSON constant: NaN', '常量'),
    ('XML declarations with DTD/entities are not supported', '实体'),
    ('Invalid workspace state: categories and documents must be lists', '工作区'),
    ('Unsupported workspace schema version 5; supported version is 1', '版本'),
    ('Cannot recover corrupt workspace cache C:/cache.json: secret', '缓存'),
    ('Please choose an application subdirectory.', '子目录'),
    ('Invalid archive path: ../escape', '安装包'),
    ('The package does not contain NotepadView.exe.', '安装包'),
    ('invalid literal for int() with base 10: secret', '数值'),
    ('unrecognized English diagnostic', '无效'),
])
def test_value_error_translates_common_reasons_without_raw_english(message, reason):
    translated = chinese_error(ValueError(message))
    assert reason in translated
    assert message not in translated and 'secret' not in translated


@pytest.mark.parametrize('message', [
    '请选择有效文件。', 'XML 不支持 DTD 或实体声明。',
    r'目标路径无效：C:\资料\NotepadView.exe',
])
def test_chinese_messages_keep_format_identifiers_and_paths(message):
    assert chinese_error(ValueError(message)) == message


def test_mixed_chinese_and_english_diagnostic_is_not_passed_through():
    assert 'Permission denied' not in chinese_error(ValueError('操作失败：Permission denied'))


@pytest.mark.parametrize('error,reason', [
    (SQLParseError('Maximum grouping depth exceeded (100).'), '嵌套'),
    (SQLParseError('Maximum number of tokens exceeded (10000).'), '复杂'),
    (SQLParseError('secret'), 'SQL'), (RuntimeError('secret'), '运行'),
    (TypeError('secret'), '类型'), (KeyError('secret'), '字段'),
    (RecursionError('secret'), '嵌套'), (MemoryError('secret'), '内存'),
    (Exception('secret'), '未知'),
])
def test_unknown_or_other_error_types_never_expose_raw_details(error, reason):
    message = chinese_error(error)
    assert reason in message and 'secret' not in message
    assert any('\u4e00' <= char <= '\u9fff' for char in message)


def test_future_workspace_version_preserves_versions_even_with_chinese_path():
    error = ValueError('C:/资料/cache.json: Unsupported workspace schema version 7; supported version is 1')
    message = chinese_error(error)
    assert '工作区' in message and '版本 7' in message and '版本 1' in message
    assert 'C:/资料/cache.json' in message
    assert 'Unsupported' not in message


def test_corrupt_workspace_cache_preserves_path_without_nested_diagnostics():
    path = r'C:\资料\workspace.json'
    error = ValueError(f'Cannot recover corrupt workspace cache {path}: corrupt file; broken backup')
    message = chinese_error(error)
    assert path in message and '损坏' in message and '备份' in message
    assert 'corrupt file' not in message and 'broken backup' not in message


@pytest.mark.parametrize('message', [
    'JSON 格式错误：无效的 Unicode 转义序列（第 1 行，第 3 列）。',
    'XML 格式错误：CDATA 区段未闭合（第 1 行，第 5 列）。',
    '安装包中缺少 NotepadView.exe，请重新下载安装包。',
    '安装包包含无效路径：../outside.txt',
    '文件解码失败，请使用 UTF-8 编码保存。',
    r'文件系统操作失败：C:\English Folder\NotepadView.exe',
])
def test_chinese_technical_messages_are_idempotent(message):
    first = chinese_error(ValueError(message))
    assert first == message
    assert chinese_error(ValueError(first)) == first


@pytest.mark.parametrize('error', [
    FileExistsError('所选目录不是空目录，请先卸载旧版本或选择其他目录。'),
    OSError('安装包中缺少 NotepadView.exe，请重新下载安装包。'),
])
def test_os_error_preserves_custom_chinese_installation_advice(error):
    assert chinese_error(error) == str(error)


def test_os_error_preserves_chinese_reason_and_structured_path():
    error = OSError(errno.EEXIST, '所选目录非空，请先卸载旧版本。', 'C:/English Folder/app')
    message = chinese_error(error)
    assert error.strerror in message and error.filename in message


def test_utf8_decode_error_explicitly_names_supported_encoding():
    error = UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'invalid start byte')
    message = chinese_error(error)
    assert 'UTF-8' in message and '解码' in message
    assert chinese_error(ValueError(message)) == message
