import pytest
from PySide6.QtWidgets import QMessageBox
from test_ui import app, window


@pytest.mark.parametrize('name', ['payload.txt', 'payload.log', 'payload.odd', 'payload'])
def test_content_json_overrides_suffix(window, tmp_path, name):
    path = tmp_path / name
    path.write_text('{"中文": 1}', encoding='utf-8')
    assert window.open_document_path(path)
    assert window.editor.language == 'json'


def test_binary_never_launches_system(window, tmp_path, monkeypatch):
    from PySide6.QtGui import QDesktopServices
    path = tmp_path / 'payload.exe'
    path.write_bytes(b'MZ\x00\xff')
    opened, notices = [], []
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: notices.append(a[2]))
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda u: opened.append(u) or True)
    assert not window.open_document_path(path)
    assert not opened and notices and not window.sessions


@pytest.mark.parametrize('source,expected', [
    ('{"a":1}\n{"b":2}\n', 'jsonl'),
    (r'{\"items\":[]}', 'json'),
    ('{foo: () => 1}', 'js'),
    ('<root><item /></root>', 'xml'),
    ('<!DOCTYPE html><html><script>alert(1)</script></html>', 'html'),
    ('---\nname: sample\nitems:\n  - one\n', 'yaml'),
    ('# Title\n\n- one\n- two', 'md'),
    ('select name from users where id=1;', 'sql'),
    ('def greet(name):\n    return name\n', 'python'),
    ('const greet = (name) => name;', 'js'),
    ('interface User { name: string; }', 'ts'),
    ('body { color: red; }', 'css'),
    ('$color: red;\nbody { color: $color; }', 'scss'),
    ('[server]\nhost=localhost\nport=8080', 'ini'),
    ('[server]\nhost = "localhost"\nport = 8080', 'toml'),
    ('name,age\nAlice,20\nBob,30\n', 'csv'),
    ('name\tage\nAlice\t20\nBob\t30\n', 'tsv'),
    ('ordinary words\nmore ordinary words', 'txt'),
])
def test_detection(source, expected):
    from notepadview.text_types import detect_text_type
    assert detect_text_type('sample.odd', source) == expected


def test_manual_type_survives_save_rename_restore(window, tmp_path):
    path = tmp_path / 'data.odd'
    path.write_text('{"x":1}', encoding='utf-8')
    assert window.open_document_path(path)
    assert window.set_document_type('yaml')
    assert window.save_document()
    assert window.editor.language == 'yaml'
    assert window.rename_document_session(window.current_session, 'renamed.odd')
    assert window.flush_workspace()
    restored = type(window)(settings=window.settings)
    try:
        assert restored.editor.language == 'yaml'
        assert restored.current_session.manual_format
        assert restored.save_document()
        assert restored.editor.language == 'yaml'
    finally:
        restored.close()


@pytest.mark.parametrize('language', ['python', 'yaml', 'json', 'txt'])
def test_explicit_new_type_survives_unknown_suffix_save(window, tmp_path, language):
    window.new_document(language)
    window.editor.setPlainText('ordinary words')
    assert window.current_session.manual_format
    assert window.save_to(tmp_path / 'chosen.odd')
    assert window.editor.language == language
    assert window.current_session.document_format == language


def test_python_draft_default_name_and_rename(window, monkeypatch):
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: QMessageBox.Ok)
    window.new_document('python')
    assert window.current_session.name == '未命名.py'
    from pathlib import Path
    from PySide6.QtWidgets import QFileDialog
    suggested = []
    monkeypatch.setattr(QFileDialog, 'getSaveFileName',
                        lambda *args, **kwargs: suggested.append(Path(args[2]).name) or ('', ''))
    assert not window.save_as()
    assert suggested == ['未命名.py']
    assert window.rename_document_session(window.current_session, 'script.py')
    assert window.current_session.name == 'script.py'


def test_python_draft_rename_without_suffix(window):
    window.new_document('python')
    assert window.rename_document_session(window.current_session, 'script')
    assert window.current_session.name == 'script.py'


def test_python_draft_rename_dialog_suffix(window, monkeypatch):
    from PySide6.QtWidgets import QDialog, QInputDialog
    window.new_document('python')
    labels = []
    monkeypatch.setattr(QInputDialog, 'exec', lambda dialog: labels.append(dialog.labelText()) or QDialog.Rejected)
    assert not window.request_rename_document()
    assert '.py，' in labels[0]


def test_jsonl_format_keeps_records_lexemes_and_undo(window):
    window.new_document('jsonl')
    source = '{"a": 1e+02,"a":-0}\n{"b": 2.00}\n'
    window.editor.setPlainText(source)
    assert window.format_document()
    assert window.editor.source_text() == '{"a":1e+02,"a":-0}\n{"b":2.00}\n'
    window.editor.undo()
    assert window.editor.source_text() == source


def test_registry_lexers_and_no_unsafe_formatters(window):
    from notepadview.text_types import TEXT_TYPES, lexer_for
    for key in TEXT_TYPES:
        assert lexer_for(key) is not None
        window.new_document(key)
        assert window.editor.language == key
        if key in ('yaml', 'html', 'css', 'scss', 'js', 'ts', 'python', 'ini', 'toml', 'csv', 'tsv'):
            assert not window.format_document_action.isEnabled()
            assert not window.format_document()

@pytest.mark.parametrize('name,expected', [('data.json', 'json'), ('data.jsonl', 'jsonl'), ('data.py', 'python')])
def test_known_suffix_precedes_conflicting_content(window, tmp_path, name, expected):
    path = tmp_path / name
    path.write_text('{"a":1}\n{"b":2}\n' if expected != 'jsonl' else '{"a":1}', encoding='utf-8')
    assert window.open_document_path(path)
    assert window.editor.language == expected


def test_save_as_changed_suffix_clears_manual_type(window, tmp_path):
    path = tmp_path / 'data.jsonl'
    path.write_text('{"a":1}\n{"b":2}\n', encoding='utf-8')
    assert window.open_document_path(path)
    assert window.set_document_type('yaml')
    assert window.save_to(tmp_path / 'data.json')
    assert window.editor.language == 'json'
    assert not window.current_session.manual_format


def test_reopen_same_suffix_keeps_manual_type(window, tmp_path, monkeypatch):
    path = tmp_path / 'data.json'
    path.write_text('{"a":1}', encoding='utf-8')
    assert window.open_document_path(path)
    assert window.set_document_type('yaml')
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.Yes)
    assert window.open_document_path(path)
    assert window.editor.language == 'yaml'


def test_switch_after_path_suffix_change_preserves_dirty_text(window, tmp_path):
    path = tmp_path / 'data.jsonl'
    path.write_text('{"a":1}\n{"b":2}', encoding='utf-8')
    assert window.open_document_path(path)
    session = window.current_session
    assert window.set_document_type('yaml')
    window.editor.insertPlainText('unsaved')
    source = window.editor.source_text()
    window.new_document('txt')
    session.path = tmp_path / 'data.json'
    window.document_tabs.setCurrentIndex(window._session_tab_index(session))
    assert window.editor.language == 'json'
    assert not session.manual_format
    assert window.editor.source_text() == source
    assert session.document.isModified()


@pytest.mark.parametrize('manual', [False, True])
def test_restore_refreshes_suffix_without_reloading_cached_text(window, tmp_path, manual):
    path = tmp_path / 'data.jsonl'
    path.write_text('{"a":1}\n{"b":2}', encoding='utf-8')
    assert window.open_document_path(path)
    if manual:
        assert window.set_document_type('yaml')
    window.editor.insertPlainText('unsaved')
    source = window.editor.source_text()
    state = window.workspace.snapshot()
    state['documents'][0]['path'] = str(tmp_path / 'data.json')
    window.workspace.store.save(state)
    restored = type(window)(settings=window.settings)
    try:
        assert restored.editor.language == 'json'
        assert not restored.current_session.manual_format
        assert restored.editor.source_text() == source
        assert restored.current_session.document.isModified()
    finally:
        restored.close()


def test_external_rename_open_does_not_replace_old_dirty_session(window, tmp_path):
    path = tmp_path / 'data.jsonl'
    path.write_text('{"a":1}\n{"b":2}', encoding='utf-8')
    assert window.open_document_path(path)
    old = window.current_session
    window.editor.insertPlainText('unsaved')
    source = window.editor.source_text()
    target = path.with_suffix('.json')
    path.rename(target)
    assert window.open_document_path(target)
    assert len(window.sessions) == 2
    assert window.editor.language == 'json'
    assert old.path == path
    assert bytes(old.document.get_char_range(0, old.document.length())).decode('utf-8') == source


def test_reopen_refreshes_cached_automatic_type(window, tmp_path, monkeypatch):
    path = tmp_path / 'data.json'
    path.write_text('{"a":1}\n{"b":2}', encoding='utf-8')
    assert window.open_document_path(path)
    window.current_session.document_format = 'jsonl'
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.Yes)
    assert window.open_document_path(path)
    assert window.editor.language == 'json'


def test_unknown_suffix_after_save_as_uses_content(window, tmp_path):
    path = tmp_path / 'data.jsonl'
    path.write_text('{"a":1}', encoding='utf-8')
    assert window.open_document_path(path)
    assert window.set_document_type('yaml')
    assert window.save_to(tmp_path / 'data.odd')
    assert window.editor.language == 'json'
    assert not window.current_session.manual_format
