from contextlib import closing
import json
from pathlib import Path
import sqlite3
from threading import Lock

from notepadview.api_workbench.models import (
    ApiCollection, Environment, RequestHistory,
)


_SCHEMA_VERSION = 5
_MAX_HISTORY = 100


class ApiWorkbenchStore:
    def __init__(self, path, vault):
        self.path = Path(path)
        self.vault = vault
        self._lock = Lock()
        self._closed = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA secure_delete=ON')
        return connection

    def _encrypt_existing_requests(self, connection):
        rows = connection.execute('SELECT id, payload FROM request_definitions').fetchall()
        for request_id, payload in rows:
            raw = payload.encode('utf-8') if isinstance(payload, str) else bytes(payload)
            connection.execute(
                'UPDATE request_definitions SET payload=? WHERE id=?',
                (self.vault.encrypt(raw), request_id))

    def _encrypt_existing_environments(self, connection):
        rows = connection.execute('SELECT id, payload FROM environments').fetchall()
        for environment_id, payload in rows:
            raw = payload.encode('utf-8') if isinstance(payload, str) else bytes(payload)
            connection.execute(
                'UPDATE environments SET payload=? WHERE id=?',
                (self.vault.encrypt(raw), environment_id))

    def _initialize(self):
        with closing(self._connect()) as connection:
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version > _SCHEMA_VERSION:
                raise ValueError(
                    f'Unsupported API workbench schema version {version}; '
                    f'supported version is {_SCHEMA_VERSION}')
            if version == 0:
                tables = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
                if tables:
                    raise ValueError('Unversioned API workbench database is not empty')
                try:
                    connection.execute('BEGIN IMMEDIATE')
                    connection.execute(
                        'CREATE TABLE collections ('
                        'id TEXT PRIMARY KEY, name TEXT NOT NULL, source TEXT NOT NULL, '
                        'description TEXT NOT NULL)')
                    connection.execute(
                        'CREATE TABLE operations ('
                        'id TEXT PRIMARY KEY, collection_id TEXT NOT NULL REFERENCES collections(id) '
                        'ON DELETE CASCADE, position INTEGER NOT NULL, payload TEXT NOT NULL, '
                        'UNIQUE(collection_id, position))')
                    connection.execute(
                        'CREATE TABLE environments (id TEXT PRIMARY KEY, payload BLOB NOT NULL)')
                    connection.execute(
                        'CREATE TABLE variables ('
                        'scope_type TEXT NOT NULL, scope_id TEXT NOT NULL, name TEXT NOT NULL, '
                        'value BLOB NOT NULL, secret INTEGER NOT NULL CHECK(secret IN (0, 1)), '
                        'PRIMARY KEY(scope_type, scope_id, name))')
                    connection.execute(
                        'CREATE TABLE collection_variables ('
                        'collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE, '
                        'name TEXT NOT NULL, value BLOB NOT NULL, '
                        'secret INTEGER NOT NULL CHECK(secret IN (0, 1)), '
                        'PRIMARY KEY(collection_id, name))')
                    connection.execute(
                        'CREATE TABLE request_definitions ('
                        'id TEXT PRIMARY KEY, collection_id TEXT REFERENCES collections(id) '
                        'ON DELETE CASCADE, position INTEGER NOT NULL, payload BLOB NOT NULL, '
                        'UNIQUE(collection_id, position))')
                    connection.execute(
                        'CREATE TABLE cookies ('
                        'scope_id TEXT NOT NULL, name TEXT NOT NULL, value BLOB NOT NULL, '
                        'PRIMARY KEY(scope_id, name))')
                    connection.execute(
                        'CREATE TABLE secrets (name TEXT PRIMARY KEY, value BLOB NOT NULL)')
                    connection.execute(
                        'CREATE TABLE history ('
                        'id TEXT PRIMARY KEY, completed_at TEXT NOT NULL, payload BLOB NOT NULL)')
                    connection.execute(f'PRAGMA user_version={_SCHEMA_VERSION}')
                    connection.commit()
                except sqlite3.Error:
                    connection.rollback()
                    raise
            elif version in (1, 2, 3, 4):
                try:
                    connection.execute('BEGIN IMMEDIATE')
                    if version == 1:
                        connection.execute(
                            'ALTER TABLE request_definitions ADD COLUMN '
                            'position INTEGER NOT NULL DEFAULT 0')
                        collections = connection.execute(
                            'SELECT DISTINCT collection_id FROM request_definitions '
                            'WHERE collection_id IS NOT NULL').fetchall()
                        for (collection_id,) in collections:
                            rows = connection.execute(
                                'SELECT id FROM request_definitions '
                                'WHERE collection_id=? ORDER BY rowid',
                                (collection_id,)).fetchall()
                            connection.executemany(
                                'UPDATE request_definitions SET position=? WHERE id=?',
                                [(position, row[0]) for position, row in enumerate(rows)])
                        connection.execute(
                            'CREATE UNIQUE INDEX request_order '
                            'ON request_definitions(collection_id, position)')
                    if version in (1, 2):
                        self._encrypt_existing_requests(connection)
                    if version in (1, 2, 3):
                        connection.execute(
                            'CREATE TABLE collection_variables ('
                            'collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE, '
                            'name TEXT NOT NULL, value BLOB NOT NULL, '
                            'secret INTEGER NOT NULL CHECK(secret IN (0, 1)), '
                            'PRIMARY KEY(collection_id, name))')
                    self._encrypt_existing_environments(connection)
                    connection.execute(f'PRAGMA user_version={_SCHEMA_VERSION}')
                    connection.commit()
                except sqlite3.Error:
                    connection.rollback()
                    raise
            elif connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('API workbench database integrity check failed')
            if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('API workbench database integrity check failed')

    @staticmethod
    def _json_bytes(value):
        return json.dumps(value, ensure_ascii=False, allow_nan=False,
                          separators=(',', ':')).encode('utf-8')

    def _ensure_open(self):
        if self._closed:
            raise RuntimeError(f'API workbench store is closed: {self.path}')

    def save_collection(self, collection):
        if not isinstance(collection, ApiCollection):
            raise TypeError('collection must be an ApiCollection')
        payload = collection.to_dict()
        operations = payload.pop('operations')
        requests = payload.pop('requests')
        variables = payload.pop('variables')
        variable_rows = []
        for name, variable in variables.items():
            value = variable['value'].encode('utf-8')
            secret = variable['secret']
            if secret:
                value = self.vault.encrypt(value)
            variable_rows.append((collection.id, name, value, int(secret)))
        with self._lock:
            self._ensure_open()
            with closing(self._connect()) as connection:
                with connection:
                    connection.execute(
                        'INSERT INTO collections(id, name, source, description) VALUES (?, ?, ?, ?) '
                        'ON CONFLICT(id) DO UPDATE SET name=excluded.name, source=excluded.source, '
                        'description=excluded.description',
                        (collection.id, collection.name, collection.source, collection.description))
                    connection.execute('DELETE FROM operations WHERE collection_id=?',
                                       (collection.id,))
                    connection.execute('DELETE FROM request_definitions WHERE collection_id=?',
                                       (collection.id,))
                    connection.execute(
                        'DELETE FROM collection_variables WHERE collection_id=?',
                        (collection.id,))
                    connection.executemany(
                        'INSERT INTO operations(id, collection_id, position, payload) '
                        'VALUES (?, ?, ?, ?)',
                        [(operation['id'], collection.id, index,
                          json.dumps(operation, ensure_ascii=False, allow_nan=False,
                                     separators=(',', ':')))
                         for index, operation in enumerate(operations)])
                    connection.executemany(
                        'INSERT INTO request_definitions(id, collection_id, position, payload) '
                        'VALUES (?, ?, ?, ?)',
                        [(request['id'], collection.id, index,
                          self.vault.encrypt(self._json_bytes(request)))
                         for index, request in enumerate(requests)])
                    connection.executemany(
                        'INSERT INTO collection_variables(collection_id, name, value, secret) '
                        'VALUES (?, ?, ?, ?)', variable_rows)

    def get_collection(self, collection_id):
        with self._lock:
            self._ensure_open()
            with closing(self._connect()) as connection:
                row = connection.execute(
                    'SELECT id, name, source, description FROM collections WHERE id=?',
                    (collection_id,)).fetchone()
                if row is None:
                    return None
                operations = connection.execute(
                    'SELECT payload FROM operations WHERE collection_id=? ORDER BY position',
                    (collection_id,)).fetchall()
                requests = connection.execute(
                    'SELECT payload FROM request_definitions WHERE collection_id=? ORDER BY position',
                    (collection_id,)).fetchall()
                variables = connection.execute(
                    'SELECT name, value, secret FROM collection_variables '
                    'WHERE collection_id=? ORDER BY name', (collection_id,)).fetchall()
        collection_variables = {}
        for name, value, secret in variables:
            raw = self.vault.decrypt(value) if secret else value
            collection_variables[name] = {
                'value': raw.decode('utf-8'), 'secret': bool(secret)}
        data = {
            'id': row[0], 'name': row[1], 'source': row[2], 'description': row[3],
            'operations': [json.loads(operation[0]) for operation in operations],
            'requests': [json.loads(self.vault.decrypt(request[0]).decode('utf-8'))
                         for request in requests],
            'variables': collection_variables,
        }
        return ApiCollection.from_dict(data)

    def list_collections(self):
        with self._lock:
            self._ensure_open()
            with closing(self._connect()) as connection:
                identifiers = connection.execute(
                    'SELECT id FROM collections ORDER BY name COLLATE NOCASE, id').fetchall()
        return [self.get_collection(row[0]) for row in identifiers]

    def delete_collection(self, collection_id):
        if not isinstance(collection_id, str) or not collection_id.strip():
            raise ValueError('Collection id must not be empty')
        with self._lock:
            self._ensure_open()
            with closing(self._connect()) as connection:
                with connection:
                    connection.execute(
                        'DELETE FROM collections WHERE id=?', (collection_id,))

    def delete_collection_items(self, items):
        if not isinstance(items, (list, tuple)):
            raise ValueError('Collection items must be a sequence')
        table_by_type = {
            'operation': 'operations',
            'request': 'request_definitions',
        }
        normalized = []
        for item in items:
            if not isinstance(item, (list, tuple)) or len(item) != 3:
                raise ValueError('Each collection item must contain an archive, type, and id')
            collection_id, item_type, item_id = item
            if (not isinstance(collection_id, str) or not collection_id.strip()
                    or not isinstance(item_id, str) or not item_id.strip()
                    or item_type not in table_by_type):
                raise ValueError('Collection item identifier or type is invalid')
            normalized.append((collection_id, table_by_type[item_type], item_id))
        if not normalized:
            return
        with self._lock:
            self._ensure_open()
            with closing(self._connect()) as connection:
                with connection:
                    for collection_id, table, item_id in normalized:
                        connection.execute(
                            f'DELETE FROM {table} WHERE collection_id=? AND id=?',
                            (collection_id, item_id))

    def save_environment(self, environment):
        if not isinstance(environment, Environment):
            raise TypeError('environment must be an Environment')
        data = environment.to_dict()
        variables = data.pop('variables')
        encoded = self.vault.encrypt(self._json_bytes(data))
        variable_rows = []
        for name, variable in variables.items():
            value = variable['value'].encode('utf-8')
            secret = variable['secret']
            if secret:
                value = self.vault.encrypt(value)
            variable_rows.append(('environment', environment.id, name, value, int(secret)))
        with self._lock:
            self._ensure_open()
            with closing(self._connect()) as connection:
                with connection:
                    connection.execute(
                        'INSERT INTO environments(id, payload) VALUES (?, ?) '
                        'ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                        (environment.id, encoded))
                    connection.execute(
                        'DELETE FROM variables WHERE scope_type=? AND scope_id=?',
                        ('environment', environment.id))
                    connection.executemany(
                        'INSERT INTO variables(scope_type, scope_id, name, value, secret) '
                        'VALUES (?, ?, ?, ?, ?)', variable_rows)

    def get_environment(self, environment_id):
        with self._lock:
            self._ensure_open()
            with closing(self._connect()) as connection:
                row = connection.execute(
                    'SELECT payload FROM environments WHERE id=?', (environment_id,)).fetchone()
                if row is None:
                    return None
                variables = connection.execute(
                    'SELECT name, value, secret FROM variables '
                    'WHERE scope_type=? AND scope_id=? ORDER BY name',
                    ('environment', environment_id)).fetchall()
        data = json.loads(self.vault.decrypt(row[0]).decode('utf-8'))
        data['variables'] = {}
        for name, value, secret in variables:
            raw = self.vault.decrypt(value) if secret else value
            data['variables'][name] = {
                'value': raw.decode('utf-8'), 'secret': bool(secret),
            }
        return Environment.from_dict(data)

    def list_environments(self):
        with self._lock:
            self._ensure_open()
            with closing(self._connect()) as connection:
                identifiers = connection.execute('SELECT id FROM environments').fetchall()
        environments = [self.get_environment(row[0]) for row in identifiers]
        return sorted(environments, key=lambda item: (item.name.casefold(), item.id))

    def save_cookies(self, scope_id, cookies):
        if not isinstance(scope_id, str) or not scope_id:
            raise ValueError('Cookie scope id must not be empty')
        if not isinstance(cookies, dict) or any(
                not isinstance(name, str) or not name
                or not isinstance(value, str) for name, value in cookies.items()):
            raise ValueError('Cookies must be a string-to-string object')
        rows = [
            (scope_id, name, self.vault.encrypt(value.encode('utf-8')))
            for name, value in cookies.items()
        ]
        with self._lock:
            self._ensure_open()
            with closing(self._connect()) as connection:
                with connection:
                    connection.execute('DELETE FROM cookies WHERE scope_id=?', (scope_id,))
                    connection.executemany(
                        'INSERT INTO cookies(scope_id, name, value) VALUES (?, ?, ?)', rows)

    def get_cookies(self, scope_id):
        with self._lock:
            self._ensure_open()
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    'SELECT name, value FROM cookies WHERE scope_id=? ORDER BY name',
                    (scope_id,)).fetchall()
        return {
            name: self.vault.decrypt(value).decode('utf-8')
            for name, value in rows
        }

    def save_history(self, history):
        if not isinstance(history, RequestHistory):
            raise TypeError('history must be a RequestHistory')
        payload = self.vault.encrypt(self._json_bytes(history.to_dict()))
        with self._lock:
            self._ensure_open()
            with closing(self._connect()) as connection:
                with connection:
                    connection.execute(
                        'INSERT INTO history(id, completed_at, payload) VALUES (?, ?, ?) '
                        'ON CONFLICT(id) DO UPDATE SET completed_at=excluded.completed_at, '
                        'payload=excluded.payload',
                        (history.id, history.completed_at, payload))
                    connection.execute(
                        'DELETE FROM history WHERE id IN ('
                        'SELECT id FROM history ORDER BY completed_at DESC, id DESC '
                        'LIMIT -1 OFFSET ?)', (_MAX_HISTORY,))

    def list_history(self):
        with self._lock:
            self._ensure_open()
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    'SELECT payload FROM history ORDER BY completed_at DESC, id DESC '
                    'LIMIT ?', (_MAX_HISTORY,)).fetchall()
        return [RequestHistory.from_dict(json.loads(
            self.vault.decrypt(row[0]).decode('utf-8'))) for row in rows]

    def get_history(self, history_id):
        with self._lock:
            self._ensure_open()
            with closing(self._connect()) as connection:
                row = connection.execute(
                    'SELECT payload FROM history WHERE id=?', (history_id,)).fetchone()
        if row is None:
            return None
        return RequestHistory.from_dict(json.loads(
            self.vault.decrypt(row[0]).decode('utf-8')))

    def close(self):
        with self._lock:
            self._closed = True
