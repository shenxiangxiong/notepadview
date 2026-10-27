import importlib
from time import monotonic

import pytest
from PySide6.QtWidgets import QApplication

from notepadview.editor import SourceDocument, SourceEditor
from notepadview.search import BoundedSearch, SearchError


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def editor(app):
    instance = SourceEditor()
    instance.set_language('txt')
    yield instance
    instance.close()
    app.processEvents()


@pytest.mark.parametrize(('term', 'regular_expression', 'text', 'expected'), [
    ('cat', False, 'Cat cat scatter', 3),
    (r'item=\d+', True, 'item=1\nitem=x\nitem=22', 2),
    (r'(?=)', True, 'ab', 3),
])
def test_bounded_search_counts_all_matches(term, regular_expression, text, expected):
    assert BoundedSearch(term, regular_expression).count(text) == expected


def test_bounded_search_count_rejects_invalid_regex():
    with pytest.raises(SearchError, match='正则表达式'):
        BoundedSearch('(', True).count('text')


def test_regex_find_uses_utf8_offsets_and_cycles(editor):
    editor.setPlainText('中文 Cat 12\n猫 cat 34')
    editor.set_selection(0, 0)
    assert editor.find_text(r'cat\s+\d+', False, True)
    assert editor.selection_range() == (3, 9)
    assert editor.selected_text() == 'Cat 12'
    assert editor.find_text(r'cat\s+\d+', False, True)
    assert editor.selection_range() == (12, 18)
    assert editor.find_text(r'cat\s+\d+', False, True)
    assert editor.selection_range() == (3, 9)
    assert editor.find_text(r'cat\s+\d+', True, True)
    assert editor.selection_range() == (12, 18)


def test_literal_find_still_uses_native_scintilla(editor, monkeypatch):
    editor.setPlainText('中文 A.B aXb a.b')
    editor.set_selection(0, 0)
    monkeypatch.setattr(editor, 'source_text', lambda: pytest.fail('native search read text'))
    assert editor.find_text('a.b')
    assert editor.selected_text() == 'A.B'
    assert editor.find_text('a.b', backward=True)
    assert editor.selected_text() == 'a.b'


@pytest.mark.parametrize('pattern', [r'(?<=前)猫(?=后)', r'^猫(?=后)'])
def test_replace_selected_keeps_lookaround_and_anchor_context(editor, pattern):
    source = '前猫后' if '前' in pattern else '前\n猫后'
    editor.setPlainText(source)
    position = source.index('猫')
    editor.set_selection(position, position + 1)
    assert editor.replace_match(pattern, '狗', True)
    assert editor.source_text() == source.replace('猫', '狗')


def test_replace_does_not_accept_partial_selection_or_fake_empty_match(editor):
    editor.setPlainText('abc 123')
    editor.set_selection(0, 2)
    assert editor.replace_match(r'\d+', 'X', True)
    assert editor.source_text() == 'abc X'
    editor.setPlainText('中文')
    editor.set_selection(0, 0)
    assert editor.replace_match(r'(?<=文)$', 'X', True)
    assert editor.source_text() == '中文X'


def test_regex_replacements_expand_numeric_and_named_groups(editor):
    editor.setPlainText('中文 A12 b34')
    editor.set_selection(0, 0)
    assert editor.replace_match(r'(?P<letter>[a-z])(\d+)', r'\2-\g<letter>', True)
    assert editor.source_text() == '中文 12-A b34'
    assert editor.replace_all_matches(r'(?P<letter>[a-z])(\d+)', r'\g<letter>:\2', True) == 1
    assert editor.source_text() == '中文 12-A b:34'


@pytest.mark.parametrize('pattern, source, selection, expected', [
    (r'(?<= )(?P<part>a.*)(?=b)', '前 ab ab', (2, 3), '前 <a>b ab'),
    (r'(?P<part>a|ab)', '前 ab ab', (2, 4), '前 <ab> ab'),
])
def test_exact_selected_match_can_backtrack_without_losing_context(editor, pattern, source,
                                                                  selection, expected):
    editor.setPlainText(source)
    editor.set_selection(*selection)
    assert editor.replace_match(pattern, r'<\g<part>>', True)
    assert editor.source_text() == expected


def test_literal_replacements_are_case_insensitive_and_keep_backslashes(editor):
    editor.setPlainText('中文 CAT cat')
    editor.set_selection(3, 6)
    assert editor.replace_match('cat', r'\1\g<name>')
    assert editor.source_text() == r'中文 \1\g<name> cat'
    assert editor.replace_all_matches('CAT', r'C:\notes\1') == 1
    assert editor.source_text() == r'中文 \1\g<name> C:\notes\1'


@pytest.mark.parametrize('method', ['find_text', 'replace_match', 'replace_all_matches'])
def test_invalid_pattern_is_chinese_and_does_not_change_state(editor, method):
    search = importlib.import_module('notepadview.search')
    assert issubclass(search.SearchError, ValueError)
    editor.setPlainText('中文 abc')
    editor.set_selection(1, 3)
    args = ('(', False, True) if method == 'find_text' else ('(', 'X', True)
    with pytest.raises(search.SearchError, match='正则表达式') as error:
        getattr(editor, method)(*args)
    assert not any('a' <= char.lower() <= 'z' for char in str(error.value))
    assert editor.source_text() == '中文 abc'
    assert editor.selection_range() == (1, 3)
    assert not editor.document().isUndoAvailable()


@pytest.mark.parametrize('backward, expected', [(False, [0, 1, 2, 0]), (True, [0, 2, 1, 0])])
def test_zero_width_find_advances_and_wraps(editor, backward, expected):
    editor.setPlainText('中文')
    editor.set_selection(0, 0)
    positions = []
    for _ in expected:
        assert editor.find_text(r'(?=)', backward, True)
        positions.append(editor.selection_range()[0])
    assert positions == expected


@pytest.mark.parametrize('replacement', ['', 'X', '中文'])
def test_zero_width_replace_then_automatic_find_advances(editor, replacement):
    editor.setPlainText('abc')
    editor.set_selection(0, 0)
    assert editor.find_text(r'(?=.)', False, True)
    assert editor.replace_match(r'(?=.)', replacement, True)
    assert editor.source_text() == replacement + 'abc'
    assert editor.find_text(r'(?=.)', False, True)
    assert editor.selection_range() == (len(replacement) + 1,) * 2


def test_repeated_empty_replacements_advance_without_external_find(editor):
    editor.setPlainText('abc')
    editor.set_selection(0, 0)
    for expected in [0, 1, 2, 0]:
        assert editor.replace_match(r'(?=.)', '', True)
        assert editor.selection_range() == (expected, expected)


def test_zero_width_state_invalidates_on_query_revision_selection_and_document(editor):
    editor.setPlainText('ab')
    editor.set_selection(0, 0)
    assert editor.find_text(r'(?=)', False, True)
    assert editor.find_text(r'(?=.)', False, True)
    assert editor.selection_range() == (0, 0)
    editor.set_selection(1, 1)
    assert editor.find_text(r'(?=.)', False, True)
    assert editor.selection_range() == (1, 1)
    editor.setPlainText('ab')
    editor.set_selection(1, 1)
    assert editor.find_text(r'(?=.)', False, True)
    assert editor.selection_range() == (1, 1)
    second = SourceDocument()
    second.setPlainText('ab')
    editor.set_source_document(second, language='txt')
    editor.set_selection(1, 1)
    assert editor.find_text(r'(?=.)', False, True)
    assert editor.selection_range() == (1, 1)


@pytest.mark.parametrize('pattern, replacement, expected, count', [
    (r'^', '>', '>中文\n>abc', 2),
    (r'(?<=中)文(?=\n)', '字', '中字\nabc', 1),
    (r'(?=)', '-', '-中-文-\n-a-b-c-', 7),
])
def test_replace_all_context_and_zero_width_are_one_undo(editor, pattern, replacement, expected, count):
    source = '中文\nabc'
    editor.setPlainText(source)
    assert editor.replace_all_matches(pattern, replacement, True) == count
    assert editor.source_text() == expected
    editor.undo()
    assert editor.source_text() == source
    assert not editor.document().isUndoAvailable()
    editor.redo()
    assert editor.source_text() == expected


def test_literal_replace_all_is_one_undo(editor):
    editor.setPlainText('猫 CAT cat cat')
    assert editor.replace_all_matches('cat', '狗') == 3
    assert editor.source_text() == '猫 狗 狗 狗'
    editor.undo()
    assert editor.source_text() == '猫 CAT cat cat'
    assert not editor.document().isUndoAvailable()


@pytest.mark.parametrize('method', ['find_text', 'replace_match', 'replace_all_matches'])
def test_regex_timeout_leaves_document_selection_and_undo_unchanged(editor, method):
    search = importlib.import_module('notepadview.search')
    source = '中文 ' + 'a' * 50000 + '!'
    editor.setPlainText(source)
    editor.set_selection(0, 0)
    before = monotonic()
    args = (r'(a+)+$', False, True) if method == 'find_text' else (r'(a+)+$', 'X', True)
    with pytest.raises(search.SearchError, match='超时'):
        getattr(editor, method)(*args)
    assert monotonic() - before < 1.0
    assert editor.source_text() == source
    assert editor.selection_range() == (0, 0)
    assert not editor.document().isUndoAvailable()


def test_backward_scan_shares_one_operation_budget(editor, monkeypatch):
    search = importlib.import_module('notepadview.search')
    editor.setPlainText('x' * 100)
    editor.set_selection(100, 100)
    clock = [0.0]

    def tick():
        clock[0] += 0.01
        return clock[0]

    monkeypatch.setattr(search, 'monotonic', tick)
    with pytest.raises(search.SearchError, match='超时'):
        editor.find_text(r'x', True, True)
    assert editor.selection_range() == (100, 100)


def test_backward_find_stops_at_nearby_candidate_in_large_document(editor):
    editor.setPlainText('a' * 2000000)
    editor.set_selection(1, 1)
    assert editor.find_text('a', True, True)
    assert editor.selection_range() == (0, 1)


def test_forward_zero_width_preserves_same_position_nonempty_branch(editor):
    editor.setPlainText('a')
    editor.set_selection(0, 0)
    for expected in [(0, 0), (0, 1), (0, 0), (0, 1)]:
        assert editor.find_text(r'(?=a)|a', False, True)
        assert editor.selection_range() == expected


@pytest.mark.parametrize('replacement', ['', 'X', '中文'])
def test_zero_width_replace_preserves_same_position_nonempty_branch(editor, replacement):
    editor.setPlainText('a')
    editor.set_selection(0, 0)
    assert editor.find_text(r'(?=a)|a', False, True)
    assert editor.replace_match(r'(?=a)|a', replacement, True)
    assert editor.find_text(r'(?=a)|a', False, True)
    assert editor.selection_range() == (len(replacement), len(replacement) + 1)


@pytest.mark.parametrize('method', ['find_text', 'replace_match', 'replace_all_matches'])
@pytest.mark.parametrize('pattern, source, selection', [
    (r'(?P<prefix>[ac])\Kb', 'ab cb', (1, 2)),
    (r'aabbX|a(?R)?b', 'aabbX', (0, 4)),
    (r'(a)(?1)', 'aa', (0, 2)),
    (r'(?P<item>a)(?P>item)', 'aa', (0, 2)),
    (r'(?P<item>a)\g<item>', 'aa', (0, 2)),
    (r'(?|a|b)', 'ab', (0, 1)),
    (r'(?V1)a', 'a', (0, 1)),
    (r'(?r)a', 'a', (0, 1)),
], ids=['reset-start', 'recursion', 'subroutine', 'named-subroutine',
        'extended-backreference', 'branch-reset', 'version', 'reverse'])
def test_engine_only_extensions_are_rejected_without_mutation(editor, method, pattern,
                                                              source, selection):
    search = importlib.import_module('notepadview.search')
    editor.setPlainText(source)
    editor.set_selection(*selection)
    args = (pattern, False, True) if method == 'find_text' else (pattern, r'<\g<0>>', True)
    with pytest.raises(search.SearchError, match='正则表达式') as error:
        getattr(editor, method)(*args)
    assert not any('a' <= char.lower() <= 'z' for char in str(error.value))
    assert editor.source_text() == source
    assert editor.selection_range() == selection
    assert not editor.document().isUndoAvailable()


def test_empty_query_is_noop_in_all_modes(editor):
    editor.setPlainText('中文')
    for mode in (False, True):
        assert not editor.find_text('', False, mode)
        assert not editor.replace_match('', 'x', mode)
        assert editor.replace_all_matches('', 'x', mode) == 0
    assert editor.source_text() == '中文'


@pytest.mark.parametrize('pattern', ['(' * 1000, 'a' * 50000, '(?r)a'],
                         ids=['nested', 'too-long', 'reverse'])
def test_extreme_or_reverse_patterns_are_chinese_errors(editor, pattern):
    search = importlib.import_module('notepadview.search')
    editor.setPlainText('中文 a')
    with pytest.raises(search.SearchError, match='正则表达式'):
        editor.find_text(pattern, False, True)
    assert editor.source_text() == '中文 a'


@pytest.mark.parametrize('method', ['replace_match', 'replace_all_matches'])
def test_excessive_template_is_rejected_before_parsing(editor, method):
    search = importlib.import_module('notepadview.search')
    editor.setPlainText('中文 a')
    with pytest.raises(search.SearchError, match='替换内容过长'):
        getattr(editor, method)('a', 'x' * 100000, True)
    assert editor.source_text() == '中文 a'
    assert not editor.document().isUndoAvailable()


def test_replace_all_timeout_after_earlier_match_is_atomic(editor):
    search = importlib.import_module('notepadview.search')
    source = '猫 ' + 'a' * 50000 + '!'
    editor.setPlainText(source)
    editor.set_selection(0, 1)
    with pytest.raises(search.SearchError, match='超时'):
        editor.replace_all_matches(r'猫|(a+)+$', 'X', True)
    assert editor.source_text() == source
    assert editor.selection_range() == (0, 1)
    assert not editor.document().isUndoAvailable()


@pytest.mark.parametrize('pattern', [r'(?i)(?P<part>a|ab)',
                                     '(?x)(?P<part>a|ab) # 注释'])
def test_exact_selection_supports_conventional_inline_flags(editor, pattern):
    editor.setPlainText('中文 AB ab')
    editor.set_selection(3, 5)
    assert editor.replace_match(pattern, r'<\g<part>>', True)
    assert editor.source_text() == '中文 <AB> ab'


def test_empty_caret_before_any_search_is_not_a_selected_regex_match(editor):
    editor.setPlainText('中文 abc')
    editor.set_selection(0, 0)
    assert not editor.replace_match(r'(?<=不存在)', 'X', True)
    assert editor.source_text() == '中文 abc'
    assert editor.selection_range() == (0, 0)
    assert not editor.document().isUndoAvailable()


@pytest.mark.parametrize('method', ['replace_match', 'replace_all_matches'])
@pytest.mark.parametrize('replacement', [r'\9', r'\g<missing>', r'\g<', '\\', r'\q'])
@pytest.mark.parametrize('source', ['nothing', '猫'])
def test_invalid_template_is_checked_even_without_match(editor, method, replacement, source):
    search = importlib.import_module('notepadview.search')
    editor.setPlainText(source)
    editor.set_selection(0, len(source))
    with pytest.raises(search.SearchError, match='替换') as error:
        getattr(editor, method)('猫', replacement, True)
    assert not any('a' <= char.lower() <= 'z' for char in str(error.value))
    assert editor.source_text() == source
    assert editor.selection_range() == (0, len(source))
    assert not editor.document().isUndoAvailable()
