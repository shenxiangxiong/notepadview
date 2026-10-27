import sqlite3
from contextlib import closing
import pytest
from notepadview.workspace_store import WorkspaceStore
from test_workspace_store import _state


def test_sqlite_pdf_source_is_written_once_and_backup_recovers(tmp_path):
    store = WorkspaceStore(tmp_path / 'workspace.sqlite3')
    state = _state()
    state['documents'][0]['image_edit'] = {'version': 2, 'initial': {'data': 'a' * 100000, 'layers': []}, 'history': []}
    store.save(state)
    assets = list(tmp_path.glob('workspace.sqlite3.assets/*'))
    assert len(assets) == 1
    stamp = assets[0].stat().st_mtime_ns
    with closing(sqlite3.connect(store.path)) as connection:
        payload = connection.execute('SELECT payload FROM workspace').fetchone()[0]
        assert len(payload) < 5000
    state['documents'][0]['image_edit']['view'] = {'page': 1}
    store.save(state)
    assert assets[0].stat().st_mtime_ns == stamp
    assert store.load() == state
    store.path.write_bytes(b'broken')
    assert 'view' not in store.load()['documents'][0]['image_edit']


def test_missing_or_corrupt_pdf_asset_is_not_silently_restored(tmp_path):
    store = WorkspaceStore(tmp_path / 'workspace.sqlite3')
    state = _state()
    state['documents'][0]['image_edit'] = {'version': 2, 'initial': {'data': 'abc', 'layers': []}, 'history': []}
    store.save(state)
    asset = next(tmp_path.glob('workspace.sqlite3.assets/*'))
    asset.write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='Corrupt PDF'):
        store.load()
    asset.unlink()
    with pytest.raises(ValueError, match='Missing PDF'):
        store.load()


def test_multiple_pdf_sources_are_not_rehashed_on_view_persistence(tmp_path, monkeypatch):
    from copy import deepcopy
    import notepadview.workspace_store as module
    store = WorkspaceStore(tmp_path / 'workspace.sqlite3')
    state = _state()
    second = deepcopy(state['documents'][0])
    second['id'] = 'second'
    state['documents'].append(second)
    for index, document in enumerate(state['documents']):
        document['image_edit'] = {'version': 2, 'initial': {'data': str(index) * 10000}, 'history': []}
    store.save(state)
    monkeypatch.setattr(module.hashlib, 'sha256', lambda *_: pytest.fail('unchanged sources rehashed'))
    store.save(state)
