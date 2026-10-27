import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


def _store(path):
    from notepadview.workspace_store import WorkspaceStore
    return WorkspaceStore(path)


def _state(text='draft'):
    return {
        'version': 1,
        'categories': [{'id': 'notes', 'name': '\u7b14\u8bb0', 'expanded': True}],
        'documents': [{
            'id': 'first', 'path': None, 'format': 'md', 'text': text,
            'modified': True, 'category_id': 'notes',
            'selection': _selection(), 'first_visible_line': 2,
        }],
        'active_id': 'first',
    }


def _selection():
    return {'mode': 0, 'main': 0, 'rectangle': [0, 0, 0, 0],
            'ranges': [[0, 3, 0, 0]]}


def _backup(path):
    return path.with_name(path.name + '.bak')


def _write(path, state):
    path.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')


def test_missing_cache_returns_none_without_creating_files(tmp_path):
    path = tmp_path / 'missing' / 'workspace.json'
    store = _store(str(path))
    assert store.load() is None
    store.close()
    assert not path.parent.exists()


def test_roundtrip_preserves_unicode_order_and_json_selection(tmp_path):
    path = tmp_path / '\u7f13\u5b58' / '\u5de5\u4f5c\u533a.json'
    state = _state('# \u4e2d\u6587\U0001f600\n\x00\r\n')
    state['documents'].append({
        'id': 'second', 'path': 'C:/\u8d44\u6599/\u7a7a.txt', 'format': 'txt',
        'text': '', 'modified': False, 'category_id': '',
        'selection': None,
        'first_visible_line': 0,
    })
    state['active_id'] = 'second'
    before = json.loads(json.dumps(state))
    store = _store(path)
    store.save(state)
    store.close()
    reopened = _store(path)
    assert reopened.load() == before
    assert state == before
    assert json.loads(path.read_text(encoding='utf-8')) == before
    assert '\u4e2d\u6587' in path.read_text(encoding='utf-8')
    reopened.close()


def test_roundtrip_preserves_ebook_reader_state(tmp_path):
    path = tmp_path / 'workspace.json'
    state = _state('')
    state['documents'][0].update({
        'kind': 'ebook',
        'path': 'C:/books/sample.epub',
        'reader_state': {'chapter': 3, 'scroll': 420.0, 'zoom': 1.4},
    })
    store = _store(path)

    store.save(state)

    assert store.load() == state
    store.close()


@pytest.mark.parametrize('reader_state', [
    {'chapter': -1},
    {'chapter': 0.5},
    {'zoom': 4},
    {'zoom': float('nan')},
    {'scroll': -1},
    {'scroll': float('inf')},
])
def test_rejects_invalid_ebook_reader_state(tmp_path, reader_state):
    state = _state('')
    state['documents'][0].update({
        'kind': 'ebook',
        'path': 'C:/books/sample.epub',
        'reader_state': reader_state,
    })

    with pytest.raises(ValueError, match='reader_state'):
        _store(tmp_path / 'workspace.json').save(state)


def test_empty_workspace_replaces_documents_without_resurrecting_them(tmp_path):
    path = tmp_path / 'workspace.json'
    empty = {'version': 1, 'categories': [], 'documents': [], 'active_id': None}
    store = _store(path)
    store.save(_state())
    store.save(empty)
    store.close()
    assert _store(path).load() == empty


def test_first_save_has_a_valid_recovery_backup(tmp_path):
    path = tmp_path / 'workspace.json'
    state = _state('')
    _store(path).save(state)
    assert json.loads(_backup(path).read_text(encoding='utf-8')) == state
    path.write_bytes(b'{incomplete')
    assert _store(path).load() == state


def test_backup_is_the_previous_valid_snapshot(tmp_path):
    path = tmp_path / 'workspace.json'
    store = _store(path)
    store.save(_state('previous'))
    store.save(_state('latest'))
    assert store.load() == _state('latest')
    assert json.loads(_backup(path).read_text(encoding='utf-8')) == _state('previous')


@pytest.mark.parametrize('corrupt', [b'', b'{broken', b'\xff', b'null', b'[]',
                                    b'{"version":1}'])
def test_corrupt_primary_recovers_without_modifying_either_file(tmp_path, corrupt):
    path = tmp_path / 'workspace.json'
    path.write_bytes(corrupt)
    _write(_backup(path), _state('recovered'))
    backup_before = _backup(path).read_bytes()
    assert _store(path).load() == _state('recovered')
    assert path.read_bytes() == corrupt
    assert _backup(path).read_bytes() == backup_before


def test_missing_primary_recovers_backup(tmp_path):
    path = tmp_path / 'workspace.json'
    _write(_backup(path), _state('recovered'))
    assert _store(path).load() == _state('recovered')
    assert not path.exists()


@pytest.mark.parametrize('backup_data', [None, b'{also broken', b'{"version":1}'])
def test_unrecoverable_corruption_raises_useful_error_and_blocks_save(tmp_path, backup_data):
    path = tmp_path / 'workspace.json'
    path.write_bytes(b'{broken')
    if backup_data is not None:
        _backup(path).write_bytes(backup_data)
    store = _store(path)
    for operation in (store.load, lambda: store.save(_state('replacement'))):
        with pytest.raises(ValueError, match=r'(?i)(corrupt|invalid|recover|backup)') as error:
            operation()
        assert str(path) in str(error.value)
        assert path.read_bytes() == b'{broken'
        if backup_data is not None:
            assert _backup(path).read_bytes() == backup_data


def test_corrupt_backup_without_primary_is_not_a_new_workspace(tmp_path):
    path = tmp_path / 'workspace.json'
    _backup(path).write_bytes(b'broken')
    with pytest.raises(ValueError, match=r'(?i)(corrupt|invalid|recover|backup)'):
        _store(path).load()


@pytest.mark.parametrize('version', [2, 999])
def test_future_schema_refuses_fallback_and_overwrite(tmp_path, version):
    path = tmp_path / 'workspace.json'
    future = dict(_state('future'), version=version)
    _write(path, future)
    _write(_backup(path), _state('older'))
    before = path.read_bytes()
    backup_before = _backup(path).read_bytes()
    store = _store(path)
    for operation in (store.load, lambda: store.save(_state('replacement'))):
        with pytest.raises(ValueError, match=r'(?i)(version|schema)') as error:
            operation()
        assert str(version) in str(error.value)
        assert str(path) in str(error.value)
    assert path.read_bytes() == before
    assert _backup(path).read_bytes() == backup_before


def test_future_backup_also_prevents_overwrite_when_primary_is_corrupt(tmp_path):
    path = tmp_path / 'workspace.json'
    path.write_bytes(b'broken')
    future = dict(_state(), version=2)
    _write(_backup(path), future)
    store = _store(path)
    with pytest.raises(ValueError, match=r'(?i)(version|schema)'):
        store.load()
    with pytest.raises(ValueError, match=r'(?i)(version|schema)'):
        store.save(_state())
    assert path.read_bytes() == b'broken'
    assert json.loads(_backup(path).read_text(encoding='utf-8')) == future


def test_saving_after_recovery_preserves_good_backup(tmp_path):
    path = tmp_path / 'workspace.json'
    path.write_bytes(b'broken')
    _write(_backup(path), _state('good backup'))
    store = _store(path)
    assert store.load() == _state('good backup')
    store.save(_state('new snapshot'))
    assert store.load() == _state('new snapshot')
    assert json.loads(_backup(path).read_text(encoding='utf-8')) == _state('good backup')


def test_valid_primary_can_replace_a_corrupt_backup(tmp_path):
    path = tmp_path / 'workspace.json'
    _write(path, _state('good'))
    _backup(path).write_bytes(b'broken')
    store = _store(path)
    assert store.load() == _state('good')
    store.save(_state('new'))
    assert store.load() == _state('new')
    assert json.loads(_backup(path).read_text(encoding='utf-8')) == _state('good')


@pytest.mark.parametrize('invalid', [None, [], {}, {'version': True},
                                    dict(_state(), version=0),
                                    dict(_state(), version=2),
                                    dict(_state(), documents=None),
                                    dict(_state(), categories={}),
                                    dict(_state(), active_id=12),
                                    dict(_state(), extra=object()),
                                    dict(_state(), extra=float('nan'))])
def test_invalid_state_does_not_replace_cache_or_backup(tmp_path, invalid):
    path = tmp_path / 'workspace.json'
    store = _store(path)
    store.save(_state('original'))
    before = {item.name: item.read_bytes() for item in tmp_path.iterdir()}
    with pytest.raises((TypeError, ValueError)):
        store.save(invalid)
    assert {item.name: item.read_bytes() for item in tmp_path.iterdir()} == before


@pytest.mark.parametrize('failure', ['backup', 'primary', 'fsync', 'verification'])
def test_failed_transaction_preserves_readable_primary_and_backup(tmp_path, monkeypatch, failure):
    path = tmp_path / 'workspace.json'
    store = _store(path)
    store.save(_state('older'))
    store.save(_state('current'))
    before = path.read_bytes()
    real_replace = os.replace
    real_read = Path.read_bytes

    def replace(source, target):
        target = Path(target)
        if target in (path, _backup(path)):
            assert Path(source).parent == path.parent
            assert json.loads(real_read(Path(source))) in (_state('current'), _state('new'))
            if (failure == 'primary' and target == path or
                    failure == 'backup' and target == _backup(path)):
                raise OSError('injected replacement failure')
        return real_replace(source, target)

    def fsync(descriptor):
        raise OSError('injected flush failure')

    def read_bytes(target):
        if target not in (path, _backup(path)):
            return b'broken staged data'
        return real_read(target)

    with monkeypatch.context() as patch:
        patch.setattr(os, 'replace', replace)
        if failure == 'fsync':
            patch.setattr(os, 'fsync', fsync)
        if failure == 'verification':
            patch.setattr(Path, 'read_bytes', read_bytes)
        with pytest.raises((OSError, ValueError)):
            store.save(_state('new'))
    assert path.read_bytes() == before
    assert store.load() == _state('current')
    assert json.loads(_backup(path).read_text(encoding='utf-8')) in (
        _state('older'), _state('current'))
    assert set(tmp_path.iterdir()) == {path, _backup(path)}


def test_cache_never_reads_or_writes_source_documents(tmp_path, monkeypatch):
    source = tmp_path / 'source.md'
    source.write_bytes(b'original source\r\n')
    missing = tmp_path / 'never-created.txt'
    state = _state('unsaved changes')
    state['documents'][0]['path'] = str(source)
    state['documents'].append(dict(state['documents'][0], id='missing', path=str(missing)))
    real_open = Path.open

    def guarded_open(target, *args, **kwargs):
        assert target not in (source, missing), 'cache accessed a source document'
        return real_open(target, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', guarded_open)
        store = _store(tmp_path / 'cache.json')
        store.save(state)
        store.save(state)
        assert store.load() == state
        store.close()
    assert source.read_bytes() == b'original source\r\n'
    assert not missing.exists()


def test_save_can_run_in_background_and_close_is_idempotent(tmp_path):
    path = tmp_path / 'workspace.json'
    store = _store(path)
    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(store.save, _state('background')).result(timeout=10)
    assert store.load() == _state('background')
    store.close()
    store.close()
    assert _store(path).load() == _state('background')


def test_io_errors_are_reported_instead_of_treated_as_corruption(tmp_path, monkeypatch):
    path = tmp_path / 'workspace.json'
    store = _store(path)
    store.save(_state('original'))
    real_read = Path.read_bytes

    def read_bytes(target):
        if target == path:
            raise PermissionError('cache is locked')
        return real_read(target)

    with monkeypatch.context() as patch:
        patch.setattr(Path, 'read_bytes', read_bytes)
        with pytest.raises(PermissionError, match='cache is locked'):
            store.load()
        with pytest.raises(PermissionError, match='cache is locked'):
            store.save(_state('new'))
    assert store.load() == _state('original')


def _invalid_nested_states():
    for collection in ('categories', 'documents'):
        for item in (None, [], 'invalid', 12):
            state = _state()
            state[collection] = [item]
            yield state, collection + '[0]'
        for field in _state()[collection][0]:
            state = _state()
            del state[collection][0][field]
            yield state, field
        for identifier in ('', None, 1, []):
            state = _state()
            state[collection][0]['id'] = identifier
            yield state, 'id'
        state = _state()
        state[collection].append(dict(state[collection][0]))
        yield state, 'id'
    for field, invalid in (
        ('name', None), ('name', 12), ('expanded', 1), ('expanded', None),
    ):
        state = _state()
        state['categories'][0][field] = invalid
        yield state, field
    for field, invalid in (
        ('path', 12), ('path', []), ('path', 'bad\x00path'),
        ('format', 'unsupported'), ('format', ''), ('format', None), ('format', []),
        ('text', None), ('text', 12), ('modified', 1), ('modified', None),
        ('category_id', None), ('category_id', []), ('category_id', 'missing'),
        ('first_visible_line', -1), ('first_visible_line', 1.5),
        ('first_visible_line', True), ('first_visible_line', None),
        ('preview_position', '0'), ('preview_position', None),
        ('preview_position', True), ('preview_position', []),
        ('preview_position', float('inf')), ('preview_position', -float('inf')),
        ('preview_position', float('nan')),
    ):
        state = _state()
        state['documents'][0][field] = invalid
        yield state, field
    for active in ('missing', ''):
        state = _state()
        state['active_id'] = active
        yield state, 'active_id'
    for invalid in ([], [0, 1], 'invalid', 12, False):
        state = _state()
        state['documents'][0]['selection'] = invalid
        yield state, 'selection'
    for field in _selection():
        state = _state()
        del state['documents'][0]['selection'][field]
        yield state, 'selection.' + field
    for field, invalid in (
        ('mode', -1), ('mode', 4), ('mode', True), ('mode', 0.0), ('mode', []),
        ('main', -1), ('main', 1), ('main', True), ('main', 0.0),
        ('rectangle', None), ('rectangle', [0, 0, 0]),
        ('rectangle', [0, 0, 0, 0, 0]), ('rectangle', ['0', 0, 0, 0]),
        ('rectangle', [-1, 0, 0, 0]), ('rectangle', [0, 0, True, 0]),
        ('ranges', None), ('ranges', []), ('ranges', [None]),
        ('ranges', [[0, 0, 0]]), ('ranges', [[0, 0, 0, 0, 0]]),
        ('ranges', [[0, -1, 0, 0]]), ('ranges', [[0, 0, 1.5, 0]]),
        ('ranges', [[False, 0, 0, 0]]), ('ranges', {'anchor': 0}),
    ):
        state = _state()
        state['documents'][0]['selection'][field] = invalid
        yield state, 'selection.' + field


@pytest.mark.parametrize('invalid,field', list(_invalid_nested_states()))
def test_nested_validation_blocks_save_and_read_but_recovers_valid_backup(tmp_path, invalid, field):
    path = tmp_path / 'workspace.json'
    store = _store(path)
    store.save(_state('original'))
    before = {item.name: item.read_bytes() for item in tmp_path.iterdir()}
    with pytest.raises(ValueError) as error:
        store.save(invalid)
    assert field in str(error.value)
    assert {item.name: item.read_bytes() for item in tmp_path.iterdir()} == before

    _write(path, invalid)
    _write(_backup(path), invalid)
    before = {item.name: item.read_bytes() for item in tmp_path.iterdir()}
    for operation in (store.load, lambda: store.save(_state('replacement'))):
        with pytest.raises(ValueError) as error:
            operation()
        assert str(path) in str(error.value)
        assert field in str(error.value) or 'Invalid JSON constant' in str(error.value)
    assert {item.name: item.read_bytes() for item in tmp_path.iterdir()} == before

    _write(_backup(path), _state('recovered'))
    before = {item.name: item.read_bytes() for item in tmp_path.iterdir()}
    assert store.load() == _state('recovered')
    assert {item.name: item.read_bytes() for item in tmp_path.iterdir()} == before
    store.save(_state('new'))
    assert store.load() == _state('new')
    assert json.loads(_backup(path).read_text(encoding='utf-8')) == _state('recovered')


@pytest.mark.parametrize('document_format', ['md', 'txt', 'json', 'xml', 'sql'])
@pytest.mark.parametrize('mode', [0, 1, 2, 3])
def test_real_selection_formats_preview_and_extra_fields_roundtrip(tmp_path, document_format, mode):
    state = _state()
    state['extra'] = {'future': [None, True, 'value']}
    state['categories'][0]['extra'] = 'kept'
    document = state['documents'][0]
    document['format'] = document_format
    document['preview_position'] = 17.25
    document['extra'] = {'kept': True}
    document['selection'] = {
        'mode': mode, 'main': 1, 'rectangle': (0, 3, 0, 2),
        'ranges': [(0, 3, 0, 0), (1, 2, 0, 1)], 'extra': 'kept',
    }
    expected = json.loads(json.dumps(state))
    store = _store(tmp_path / 'workspace.json')
    store.save(state)
    assert store.load() == expected
    assert json.loads(json.dumps(state)) == expected


@pytest.mark.parametrize('selection', [None, {}])
@pytest.mark.parametrize('preview', [0, 1.5, -1.5])
def test_empty_selection_and_uncategorized_document_are_valid(tmp_path, selection, preview):
    state = _state('')
    state['categories'] = []
    state['active_id'] = None
    state['documents'][0].update(category_id='', selection=selection,
                                 first_visible_line=0, preview_position=preview)
    store = _store(tmp_path / 'workspace.json')
    store.save(state)
    assert store.load() == state


def test_malformed_nested_backup_is_replaced_from_valid_primary(tmp_path):
    path = tmp_path / 'workspace.json'
    state = _state('valid primary')
    _write(path, state)
    invalid = _state('invalid backup')
    invalid['documents'][0]['text'] = None
    _write(_backup(path), invalid)
    store = _store(path)
    assert store.load() == state
    store.save(_state('next'))
    assert store.load() == _state('next')
    assert json.loads(_backup(path).read_text(encoding='utf-8')) == state
