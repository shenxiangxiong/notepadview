import base64
import hashlib
from pathlib import Path

import pymupdf as fitz
import pytest

from notepadview.workspace_store import WorkspaceStore
from test_workspace_store import _state


def pdf_state(text):
    state = _state(text)
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 65), text)
        source = base64.b64encode(doc.tobytes()).decode('ascii')
    state['documents'][0].update(kind='pdf', format='txt', path=str(Path('cached.pdf').resolve()), image_edit={
        'version': 2, 'initial': {'data': source, 'layers': []},
        'history': [], 'index': 0, 'clean': 0})
    return state


def asset_path(store, state):
    source = state['documents'][0]['image_edit']['initial']['data']
    digest = hashlib.sha256(source.encode('ascii')).hexdigest()
    return store.path.with_name(store.path.name + '.assets') / digest


@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_pdf_source_in_memory_repairs_damaged_asset_before_save(tmp_path, damage):
    store = WorkspaceStore(tmp_path / 'workspace.sqlite3')
    state = pdf_state('Recover')
    store.save(state)
    asset = asset_path(store, state)
    if damage == 'missing':
        asset.unlink()
    else:
        asset.write_bytes(b'broken')
    state['documents'][0]['image_edit']['view'] = {'page': 0, 'scale': 1.2}
    store.save(state)
    assert store.load() == state
    assert WorkspaceStore(store.path).load() == state


@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_failed_pdf_save_after_fallback_preserves_valid_backup(tmp_path, monkeypatch, damage):
    store = WorkspaceStore(tmp_path / 'workspace.sqlite3')
    valid = pdf_state('Valid backup')
    broken = pdf_state('Broken primary')
    store.save(valid)
    store.save(broken)
    asset = asset_path(store, broken)
    if damage == 'missing':
        asset.unlink()
    else:
        asset.write_bytes(b'broken')
    recovered = store.load()
    assert recovered == valid
    original = store._replace_sqlite

    def fail_primary(path, state):
        if path == store.path:
            raise OSError('simulated disk full')
        return original(path, state)

    monkeypatch.setattr(store, '_replace_sqlite', fail_primary)
    with pytest.raises(OSError, match='disk full'):
        store.save(recovered)
    assert store.load() == valid
    assert WorkspaceStore(store.path).load() == valid


def test_pdf_repair_keeps_plain_document_with_null_edit_state(tmp_path):
    store = WorkspaceStore(tmp_path / 'workspace.sqlite3')
    state = pdf_state('PDF')
    plain = _state('Unsaved text')['documents'][0]
    plain.update(id='plain', image_edit=None)
    state['documents'].insert(0, plain)
    store.save(state)
    assert store.load() == state


@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_closed_pdf_with_bad_asset_does_not_block_current_workspace(tmp_path, damage):
    store = WorkspaceStore(tmp_path / 'workspace.sqlite3')
    old = pdf_state('Closed PDF')
    store.save(old)
    asset = asset_path(store, old)
    if damage == 'missing':
        asset.unlink()
    else:
        asset.write_bytes(b'broken')
    current = _state('Current unsaved text')
    store.save(current)
    assert store.load() == current
    assert WorkspaceStore(store.path).load() == current


def test_replacing_asset_broken_snapshot_keeps_backup_until_success(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path / 'workspace.sqlite3')
    old = pdf_state('Closed PDF')
    store.save(old)
    asset_path(store, old).unlink()
    backup_before = store._backup.read_bytes()
    original = store._replace_sqlite

    def fail_primary(path, state):
        if path == store.path:
            raise OSError('simulated disk full')
        return original(path, state)

    monkeypatch.setattr(store, '_replace_sqlite', fail_primary)
    with pytest.raises(OSError, match='disk full'):
        store.save(_state('Current text'))
    assert store._backup.read_bytes() == backup_before
