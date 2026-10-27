import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from notepadview.workspace_store import WorkspaceStore
from test_workspace_store import _state, _write


def test_sqlite_roundtrip_backup_threads_and_close(tmp_path):
    path = tmp_path / 'workspace.sqlite3'
    store = WorkspaceStore(path)
    assert store.load() is None
    assert not path.exists()
    with ThreadPoolExecutor(1) as pool:
        pool.submit(store.save, _state('旧')).result()
        pool.submit(store.save, _state('新')).result()
    assert path.read_bytes().startswith(b'SQLite format 3')
    assert store.load() == _state('新')
    path.write_bytes(b'broken')
    assert store.load() == _state('旧')
    store.save(_state('恢复'))
    assert store.load() == _state('恢复')
    store.close()
    with pytest.raises(RuntimeError):
        store.load()


def test_readonly_legacy_migration(tmp_path):
    legacy = tmp_path / 'workspace.json'
    _write(legacy, _state('旧草稿'))
    before = legacy.read_bytes()
    path = tmp_path / 'workspace.sqlite3'
    store = WorkspaceStore(path)
    assert store.load() == _state('旧草稿')
    assert path.read_bytes().startswith(b'SQLite format 3')
    assert legacy.read_bytes() == before
    _write(legacy, _state('不应重读'))
    assert WorkspaceStore(path).load() == _state('旧草稿')


@pytest.mark.parametrize('future', [False, True])
def test_failed_legacy_migration_blocks_empty_overwrite(tmp_path, future):
    legacy = tmp_path / 'workspace.json'
    if future:
        _write(legacy, dict(_state(), version=999))
    else:
        legacy.write_bytes(b'broken')
    before = legacy.read_bytes()
    store = WorkspaceStore(tmp_path / 'workspace.sqlite3')
    with pytest.raises(ValueError):
        store.load()
    with pytest.raises(ValueError):
        store.save(_state('replacement'))
    assert legacy.read_bytes() == before
    assert not store.path.exists()


def test_sqlite_future_schema_cannot_fallback_or_overwrite(tmp_path):
    store = WorkspaceStore(tmp_path / 'workspace.sqlite3')
    store.save(_state())
    with sqlite3.connect(store.path) as connection:
        connection.execute('PRAGMA user_version=999')
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match='999'):
        store.load()
    with pytest.raises(ValueError):
        store.save(_state('replacement'))
    assert store.path.read_bytes() == before


def test_failed_transaction_keeps_primary_and_backup(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path / 'workspace.sqlite3')
    store.save(_state('previous'))
    store.save(_state('latest'))
    before = store.path.read_bytes()
    import notepadview.workspace_store as module
    replace = module.os.replace
    def fail_primary(source, target):
        if target == store.path:
            raise OSError('disk full')
        return replace(source, target)
    monkeypatch.setattr(module.os, 'replace', fail_primary)
    with pytest.raises(OSError):
        store.save(_state('lost'))
    assert store.path.read_bytes() == before
    assert store.load() == _state('latest')
    assert store._sqlite_snapshot() == _state('latest')
    assert not list(tmp_path.glob('*.staging'))


def test_corrupt_sqlite_never_falls_back_to_legacy(tmp_path):
    store = WorkspaceStore(tmp_path / 'workspace.sqlite3')
    store.path.write_bytes(b'broken')
    _write(tmp_path / 'workspace.json', _state('outdated'))
    for operation in (store.load, lambda: store.save(_state())):
        with pytest.raises(ValueError):
            operation()
    assert store.path.read_bytes() == b'broken'


def test_failed_migration_preserves_legacy_and_can_retry(tmp_path, monkeypatch):
    legacy = tmp_path / 'workspace.json'
    _write(legacy, _state('keep'))
    before = legacy.read_bytes()
    store = WorkspaceStore(tmp_path / 'workspace.sqlite3')
    with monkeypatch.context() as patch:
        patch.setattr(store, '_replace_sqlite', lambda *_: (_ for _ in ()).throw(OSError('disk full')))
        with pytest.raises(OSError):
            store.load()
        assert not store.path.exists()
    assert legacy.read_bytes() == before
    assert store.load() == _state('keep')


def test_invalid_view_fields_rejected(tmp_path):
    for field, value in [('view_mode', 'invalid'), ('last_single_view', 'split')]:
        state = _state()
        state['documents'][0][field] = value
        with pytest.raises(ValueError, match=field):
            WorkspaceStore(tmp_path / 'workspace.sqlite3').save(state)
