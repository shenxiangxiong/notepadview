import base64
import sqlite3
from datetime import datetime, timedelta, timezone
import json

import pytest

from notepadview.api_workbench.models import (
    ApiCollection, ApiOperation, Environment, RequestDefinition, RequestHistory,
)


class TestVault:
    def encrypt(self, value):
        return b'test:' + base64.b64encode(value)

    def decrypt(self, value):
        if not value.startswith(b'test:'):
            raise ValueError('Invalid test ciphertext')
        return base64.b64decode(value[5:], validate=True)


def test_store_round_trips_collection_and_environment(tmp_path):
    try:
        from notepadview.api_workbench.store import ApiWorkbenchStore
    except ImportError:
        pytest.fail('API SQLite store is not implemented')

    path = tmp_path / 'api-workbench.sqlite3'
    collection = ApiCollection(
        id='users-api',
        name='Users API',
        operations=[ApiOperation(id='list-users', method='GET', path='/users')],
        requests=[RequestDefinition(
            id='create-user', name='Create user', method='POST',
            url='https://dev.example.test/users', body_mode='raw',
            body='{"name":"Ada"}')],
    )
    environment = Environment(
        id='dev',
        name='Development',
        base_url='https://dev.example.test',
    )
    store = ApiWorkbenchStore(path, vault=TestVault())
    store.save_collection(collection)
    store.save_environment(environment)

    reopened = ApiWorkbenchStore(path, vault=TestVault())
    assert path.read_bytes().startswith(b'SQLite format 3')
    assert reopened.get_collection(collection.id) == collection
    assert reopened.get_environment(environment.id) == environment


def test_store_encrypts_request_definitions_before_persisting(tmp_path):
    from notepadview.api_workbench.store import ApiWorkbenchStore

    request = RequestDefinition(
        id='credential-request',
        name='Credential request',
        method='POST',
        url='https://example.test/login',
        auth={'type': 'bearer', 'token': 'request-token'},
        body_mode='raw',
        body='{"password":"request-password"}',
    )
    collection = ApiCollection(
        id='credentials', name='Credentials', requests=[request])
    path = tmp_path / 'encrypted-request.sqlite3'
    store = ApiWorkbenchStore(path, vault=TestVault())
    store.save_collection(collection)

    database = path.read_bytes()
    assert b'request-token' not in database
    assert b'request-password' not in database
    assert ApiWorkbenchStore(path, vault=TestVault()).get_collection(
        collection.id).requests == [request]


def test_store_keeps_only_the_latest_100_history_entries(tmp_path):
    try:
        from notepadview.api_workbench.store import ApiWorkbenchStore
    except ImportError:
        pytest.fail('API SQLite store is not implemented')

    store = ApiWorkbenchStore(tmp_path / 'history.sqlite3', vault=TestVault())
    completed = datetime(2026, 10, 25, 10, tzinfo=timezone.utc)
    for index in range(105):
        store.save_history(RequestHistory(
            id=f'history-{index}',
            completed_at=(completed + timedelta(seconds=index)).isoformat(),
            request={'method': 'GET', 'url': f'https://example.test/{index}'},
            response={'status_code': 200, 'body': str(index)},
        ))

    history = store.list_history()
    assert len(history) == 100
    assert 'history-0' not in {item.id for item in history}
    assert store.get_history('history-104').response['body'] == '104'


def test_store_rejects_future_schema_without_modifying_database(tmp_path):
    path = tmp_path / 'future.sqlite3'
    with sqlite3.connect(path) as connection:
        connection.execute('PRAGMA user_version=999')
    before = path.read_bytes()

    try:
        from notepadview.api_workbench.store import ApiWorkbenchStore
    except ImportError:
        pytest.fail('API SQLite store is not implemented')

    with pytest.raises(ValueError, match='999'):
        ApiWorkbenchStore(path, vault=TestVault())
    assert path.read_bytes() == before


def test_store_preserves_collection_request_order(tmp_path):
    from notepadview.api_workbench.store import ApiWorkbenchStore

    requests = [
        RequestDefinition(id='z-first', name='First'),
        RequestDefinition(id='a-second', name='Second'),
    ]
    collection = ApiCollection(id='ordered', name='Ordered', requests=requests)
    store = ApiWorkbenchStore(tmp_path / 'ordered.sqlite3', vault=TestVault())
    store.save_collection(collection)

    restored = store.get_collection(collection.id)
    assert [request.id for request in restored.requests] == ['z-first', 'a-second']


def test_delete_collection_cascades_to_operations_and_requests(tmp_path):
    from notepadview.api_workbench.store import ApiWorkbenchStore

    collection = ApiCollection(
        id='archive',
        name='Archive',
        operations=[ApiOperation(id='operation', method='GET', path='/items')],
        requests=[RequestDefinition(id='request', name='Saved request')],
    )
    store = ApiWorkbenchStore(tmp_path / 'delete-collection.sqlite3', vault=TestVault())
    store.save_collection(collection)

    store.delete_collection(collection.id)

    assert store.get_collection(collection.id) is None
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            'SELECT COUNT(*) FROM operations WHERE collection_id=?',
            (collection.id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            'SELECT COUNT(*) FROM request_definitions WHERE collection_id=?',
            (collection.id,),
        ).fetchone()[0] == 0


def test_delete_collection_items_removes_only_selected_requests(tmp_path):
    from notepadview.api_workbench.store import ApiWorkbenchStore

    collection = ApiCollection(
        id='archive-items',
        name='Archive',
        operations=[
        ApiOperation(id='operation-remove', method='GET', path='/remove'),
        ApiOperation(id='operation-keep', method='GET', path='/keep'),
        ],
        requests=[
        RequestDefinition(id='request-remove', name='Remove'),
        RequestDefinition(id='request-keep', name='Keep'),
        ],
    )
    store = ApiWorkbenchStore(tmp_path / 'delete-items.sqlite3', vault=TestVault())
    store.save_collection(collection)

    store.delete_collection_items([
        ('archive-items', 'operation', 'operation-remove'),
        ('archive-items', 'request', 'request-remove'),
    ])

    updated = store.get_collection(collection.id)
    assert [operation.id for operation in updated.operations] == ['operation-keep']
    assert [request.id for request in updated.requests] == ['request-keep']


def test_store_migrates_existing_schema_to_ordered_requests(tmp_path):
    path = tmp_path / 'legacy.sqlite3'
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE collections (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, source TEXT NOT NULL,
                description TEXT NOT NULL);
            CREATE TABLE operations (
                id TEXT PRIMARY KEY, collection_id TEXT NOT NULL REFERENCES collections(id)
                ON DELETE CASCADE, position INTEGER NOT NULL, payload TEXT NOT NULL,
                UNIQUE(collection_id, position));
            CREATE TABLE environments (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE variables (
                scope_type TEXT NOT NULL, scope_id TEXT NOT NULL, name TEXT NOT NULL,
                value BLOB NOT NULL, secret INTEGER NOT NULL CHECK(secret IN (0, 1)),
                PRIMARY KEY(scope_type, scope_id, name));
            CREATE TABLE request_definitions (
                id TEXT PRIMARY KEY, collection_id TEXT REFERENCES collections(id)
                ON DELETE CASCADE, payload TEXT NOT NULL);
            CREATE TABLE cookies (
                scope_id TEXT NOT NULL, name TEXT NOT NULL, value BLOB NOT NULL,
                PRIMARY KEY(scope_id, name));
            CREATE TABLE secrets (name TEXT PRIMARY KEY, value BLOB NOT NULL);
            CREATE TABLE history (
                id TEXT PRIMARY KEY, completed_at TEXT NOT NULL, payload BLOB NOT NULL);
            INSERT INTO collections VALUES ('legacy', 'Legacy', '', '');
            PRAGMA user_version=1;
        """)
        connection.execute(
            'INSERT INTO request_definitions(id, collection_id, payload) VALUES (?, ?, ?)',
            ('legacy-request', 'legacy', json.dumps({
                'id': 'legacy-request', 'name': 'Legacy', 'method': 'GET',
                'url': 'https://example.test', 'auth': {'token': 'legacy-token'},
                'query': [], 'headers': {}, 'body_mode': 'none', 'body': '',
                'pre_script': '', 'post_script': '', 'variables': {},
            })))

    from notepadview.api_workbench.store import ApiWorkbenchStore
    store = ApiWorkbenchStore(path, vault=TestVault())

    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute('PRAGMA table_info(request_definitions)')}
        version = connection.execute('PRAGMA user_version').fetchone()[0]
    assert 'position' in columns
    assert version == 5
    assert b'legacy-token' not in path.read_bytes()
    assert store.get_collection('legacy').requests[0].auth == {'token': 'legacy-token'}


def test_store_encrypts_plain_requests_when_migrating_schema_two(tmp_path):
    path = tmp_path / 'schema-two.sqlite3'
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE collections (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, source TEXT NOT NULL,
                description TEXT NOT NULL);
            CREATE TABLE operations (
                id TEXT PRIMARY KEY, collection_id TEXT NOT NULL REFERENCES collections(id)
                ON DELETE CASCADE, position INTEGER NOT NULL, payload TEXT NOT NULL,
                UNIQUE(collection_id, position));
            CREATE TABLE environments (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE variables (
                scope_type TEXT NOT NULL, scope_id TEXT NOT NULL, name TEXT NOT NULL,
                value BLOB NOT NULL, secret INTEGER NOT NULL CHECK(secret IN (0, 1)),
                PRIMARY KEY(scope_type, scope_id, name));
            CREATE TABLE request_definitions (
                id TEXT PRIMARY KEY, collection_id TEXT REFERENCES collections(id)
                ON DELETE CASCADE, position INTEGER NOT NULL, payload TEXT NOT NULL,
                UNIQUE(collection_id, position));
            CREATE TABLE cookies (
                scope_id TEXT NOT NULL, name TEXT NOT NULL, value BLOB NOT NULL,
                PRIMARY KEY(scope_id, name));
            CREATE TABLE secrets (name TEXT PRIMARY KEY, value BLOB NOT NULL);
            CREATE TABLE history (
                id TEXT PRIMARY KEY, completed_at TEXT NOT NULL, payload BLOB NOT NULL);
            INSERT INTO collections VALUES ('legacy', 'Legacy', '', '');
            PRAGMA user_version=2;
        """)
        connection.execute(
            'INSERT INTO request_definitions(id, collection_id, position, payload) '
            'VALUES (?, ?, ?, ?)',
            ('legacy-request', 'legacy', 0, json.dumps({
                'id': 'legacy-request', 'name': 'Legacy', 'method': 'GET',
                'url': 'https://example.test', 'auth': {'token': 'old-token'},
                'query': [], 'headers': {}, 'body_mode': 'none', 'body': '',
                'pre_script': '', 'post_script': '', 'variables': {},
            })))
        connection.execute(
            'INSERT INTO environments(id, payload) VALUES (?, ?)',
            ('legacy-env', json.dumps({
                'id': 'legacy-env', 'name': 'Legacy',
                'base_url': 'https://legacy.example.test',
                'default_headers': {
                    'Authorization': 'Bearer old-environment-token',
                },
            })))

    from notepadview.api_workbench.store import ApiWorkbenchStore
    store = ApiWorkbenchStore(path, vault=TestVault())

    database_bytes = path.read_bytes()
    assert b'old-token' not in database_bytes
    assert b'old-environment-token' not in database_bytes
    assert store.get_collection('legacy').requests[0].auth == {'token': 'old-token'}
    assert store.get_environment('legacy-env').default_headers == {
        'Authorization': 'Bearer old-environment-token',
    }


def test_store_encrypts_collection_variables_and_round_trips_folders(tmp_path):
    from notepadview.api_workbench.store import ApiWorkbenchStore

    path = tmp_path / 'collection-variables.sqlite3'
    request = RequestDefinition(
        id='get-pet', name='Get pet', folder='Pets', url='{{baseUrl}}/pets')
    collection = ApiCollection(
        id='pets', name='Pets', requests=[request],
        variables={'token': {'value': 'collection-token', 'secret': True}},
    )
    store = ApiWorkbenchStore(path, vault=TestVault())
    store.save_collection(collection)

    reopened = ApiWorkbenchStore(path, vault=TestVault())
    restored = reopened.get_collection(collection.id)
    assert restored == collection
    assert b'collection-token' not in path.read_bytes()


def test_store_lists_saved_collections_and_environments(tmp_path):
    from notepadview.api_workbench.store import ApiWorkbenchStore

    store = ApiWorkbenchStore(tmp_path / 'lists.sqlite3', vault=TestVault())
    collection = ApiCollection(id='pets', name='Pets')
    environment = Environment(id='dev', name='Development', base_url='https://api.example.test')
    store.save_collection(collection)
    store.save_environment(environment)

    assert store.list_collections() == [collection]
    assert store.list_environments() == [environment]


def test_store_encrypts_environment_cookies_at_rest(tmp_path):
    from notepadview.api_workbench.store import ApiWorkbenchStore

    path = tmp_path / 'cookies.sqlite3'
    store = ApiWorkbenchStore(path, vault=TestVault())
    store.save_cookies('dev', {'session': 'cookie-secret'})

    assert b'cookie-secret' not in path.read_bytes()
    assert store.get_cookies('dev') == {'session': 'cookie-secret'}


def test_store_encrypts_environment_payload_and_default_header_secrets(tmp_path):
    from notepadview.api_workbench.store import ApiWorkbenchStore

    path = tmp_path / 'environment-secrets.sqlite3'
    store = ApiWorkbenchStore(path, vault=TestVault())
    environment = Environment(
        id='dev', name='Development', base_url='https://api.example.test',
        default_headers={'Authorization': 'Bearer header-secret'},
    )
    store.save_environment(environment)

    assert b'header-secret' not in path.read_bytes()
    assert store.get_environment('dev') == environment
