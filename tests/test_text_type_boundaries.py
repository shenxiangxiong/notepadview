import pytest
from notepadview.document import BinaryDocumentError, load_text_document
from notepadview.text_types import detect_text_type, lexer_for
from pygments.token import Token


@pytest.mark.parametrize('source', ['{"foo": () => 1}', '{"foo": function() { return 1; }}', '{"foo": undefined}', '{"foo": value}'])
def test_javascript_object_is_not_json(source):
    assert detect_text_type('sample.odd', source) != 'json'


@pytest.mark.parametrize('suffix', ['exe', 'mp3', 'mp4', 'zip', '7z', 'rar'])
def test_known_binary_suffix_rejected_even_with_ascii_payload(tmp_path, suffix):
    path = tmp_path / ('payload.' + suffix)
    path.write_bytes(b'ASCII payload only')
    with pytest.raises(BinaryDocumentError):
        load_text_document(path)


@pytest.mark.parametrize('language,source', [('csv', 'name,age\n"Alice, B",20'), ('tsv', 'name\tage\nAlice\t20')])
def test_delimited_lexer_has_non_plain_tokens(language, source):
    assert any(token not in Token.Text for _, token, value in lexer_for(language).get_tokens_unprocessed(source))


def test_large_json_sample_and_suffix_fallback():
    assert detect_text_type('data.odd', '{"items":[' + '0,' * 100000 + '0]}') == 'json'
    assert detect_text_type('data.yaml', 'name: value\n') == 'yaml'
    assert detect_text_type('data.odd', 'name: value\n') == 'txt'


@pytest.mark.parametrize('item', ['0', '"hello"', 'true', 'null'])
def test_large_json_scalar_array(item):
    source = '[' + (item + ',') * 40000 + item + ']'
    assert detect_text_type('data.odd', source) == 'json'


@pytest.mark.parametrize('ending', [
    '0].map(Number)', '0];', 'undefined]', 'NaN]', 'Infinity]',
    '0,]', '0] // comment', '0] + []', '0]; const x = 1;',
])
def test_large_non_json_javascript_array(ending):
    source = '[' + '0,' * 40000 + ending
    assert detect_text_type('data.odd', source) != 'json'
    assert detect_text_type('data.js', source) == 'js'


def test_oversized_array_conservatively_keeps_suffix():
    source = '[' + '0,' * (1024 * 1024) + '0]'
    assert detect_text_type('data.odd', source) == 'txt'
    assert detect_text_type('data.js', source) == 'js'


def test_html_preview_does_not_embed_executable_source():
    from notepadview.rendering import render_code_preview
    html = render_code_preview('<script>window.hacked = true</script>', 'html')
    assert '<script>' not in html
    assert 'default-src \'none\'' in html


def test_xml_declaration_encoding_is_not_changed():
    from notepadview.formatting import format_xml
    assert 'encoding="gb18030"' in format_xml('<?xml version="1.0" encoding="gb18030"?><root><a/></root>')


def test_self_closing_xml_is_detected():
    assert detect_text_type('data.odd', '<root attr="value"/>') == 'xml'


def test_jsonl_invalid_record_rejected_without_partial_output():
    from notepadview.formatting import format_jsonl
    with pytest.raises(ValueError, match='第 2 行'):
        format_jsonl('{"a":1}\n{broken}\n')
