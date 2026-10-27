import json
import hashlib
import math
import os
import sqlite3
from pathlib import Path
import tempfile
from threading import Lock
from contextlib import closing
from uuid import uuid4


class _FutureVersionError(ValueError):
    pass


class _PdfAssetError(ValueError):
    pass


def _require(condition, field, expected):
    if not condition:
        raise ValueError(f'Invalid workspace state: {field} {expected}')


def _nonnegative_integer(value):
    return type(value) is int and value >= 0


def _validate_selection(selection, field):
    _require(selection is None or isinstance(selection, dict), field, 'must be an object or null')
    if not selection:
        return
    mode = selection.get('mode')
    _require(type(mode) is int and mode in (0, 1, 2, 3),
             field + '.mode', 'must be a selection mode from 0 to 3')
    rectangle = selection.get('rectangle')
    _require(isinstance(rectangle, (list, tuple)) and len(rectangle) == 4 and
             all(_nonnegative_integer(value) for value in rectangle),
             field + '.rectangle', 'must contain four nonnegative integers')
    ranges = selection.get('ranges')
    _require(isinstance(ranges, (list, tuple)) and len(ranges) > 0,
             field + '.ranges', 'must be a nonempty list of ranges')
    for index, positions in enumerate(ranges):
        _require(isinstance(positions, (list, tuple)) and len(positions) == 4 and
                 all(_nonnegative_integer(value) for value in positions),
                 f'{field}.ranges[{index}]', 'must contain four nonnegative integers')
    main = selection.get('main')
    _require(_nonnegative_integer(main) and main < len(ranges),
             field + '.main', 'must reference an existing range')


def _validate(state):
    if not isinstance(state, dict):
        raise ValueError('Invalid workspace state: expected a JSON object')
    version = state.get('version')
    if type(version) is int and version > 1:
        raise _FutureVersionError(f'Unsupported workspace schema version {version}; supported version is 1')
    if type(version) is not int or version != 1:
        raise ValueError(f'Invalid workspace schema version {version!r}; expected 1')
    if not isinstance(state.get('categories'), list) or not isinstance(state.get('documents'), list):
        raise ValueError('Invalid workspace state: categories and documents must be lists')
    if 'active_id' not in state or (state['active_id'] is not None and
                                    not isinstance(state['active_id'], str)):
        raise ValueError('Invalid workspace state: active_id must be a string or null')
    category_ids = {''}
    document_ids = set()
    for collection, identifiers in (('categories', category_ids), ('documents', document_ids)):
        for index, item in enumerate(state[collection]):
            field = f'{collection}[{index}]'
            _require(isinstance(item, dict), field, 'must be an object')
            identifier = item.get('id')
            _require(isinstance(identifier, str) and bool(identifier) and identifier not in identifiers,
                     field + '.id', 'must be a unique nonempty string')
            identifiers.add(identifier)
            if collection == 'categories':
                _require(isinstance(item.get('name'), str), field + '.name', 'must be a string')
                _require(type(item.get('expanded')) is bool, field + '.expanded', 'must be a boolean')
                continue
            _require('path' in item and (item['path'] is None or
                     isinstance(item['path'], str) and '\x00' not in item['path']),
                     field + '.path', 'must be a string without null bytes or null')
            from notepadview.text_types import TEXT_TYPES
            _require(isinstance(item.get('format'), str) and item['format'] in TEXT_TYPES,
                     field + '.format', 'must be a registered text type')
            _require(type(item.get('manual_format', False)) is bool,
                     field + '.manual_format', 'must be a boolean')
            _require(item.get('format_suffix') is None or isinstance(item['format_suffix'], str),
                     field + '.format_suffix', 'must be a string or null')
            _require(item.get('view_mode', 'split') in ('source', 'preview', 'split'),
                     field + '.view_mode', 'must be source, preview or split')
            _require(item.get('last_single_view', 'source') in ('source', 'preview'),
                     field + '.last_single_view', 'must be source or preview')
            kind = item.get('kind', 'text')
            _require(kind in ('text', 'image', 'pdf', 'office_pdf', 'ebook', 'diagram', 'docx', 'xlsx'), field + '.kind', 'must be a supported document kind')
            _require(kind in ('text', 'diagram', 'docx', 'xlsx') or bool(item['path']), field + '.path', 'is required for previews')
            if kind in ('docx', 'xlsx'):
                _require(isinstance(item.get('office_edit'), dict) or bool(item['path']),
                         field + '.office_edit', 'is required for unsaved Office documents')
            if kind == 'diagram':
                from notepadview.diagram_formats import validate
                validate(item.get('diagram_model'))
            reader_state = item.get('reader_state', {})
            _require(isinstance(reader_state, dict) and
                     (kind == 'ebook' or not reader_state),
                     field + '.reader_state', 'must be an object for ebook documents only')
            if reader_state:
                chapter = reader_state.get('chapter', 0)
                zoom = reader_state.get('zoom', 1.0)
                scroll = reader_state.get('scroll', 0.0)
                _require(_nonnegative_integer(chapter),
                         field + '.reader_state.chapter', 'must be a nonnegative integer')
                _require(type(zoom) in (int, float) and math.isfinite(zoom)
                         and 0.5 <= zoom <= 3,
                         field + '.reader_state.zoom', 'must be between 0.5 and 3')
                _require(type(scroll) in (int, float) and math.isfinite(scroll)
                         and scroll >= 0,
                         field + '.reader_state.scroll', 'must be a nonnegative finite number')
            _require(item.get('encoding', 'utf-8') in ('utf-8', 'utf-8-sig', 'utf-16', 'utf-32',
                     'utf-16-le', 'utf-16-be', 'utf-32-le', 'utf-32-be', 'gb18030', 'ansi'),
                     field + '.encoding', 'must be a supported encoding')
            _require(item.get('newline', '\n') in ('\n', '\r\n', '\r'), field + '.newline', 'must be a newline')
            preview = item.get('preview_state', {})
            _require(isinstance(preview, dict), field + '.preview_state', 'must be an object')
            if preview:
                _require(type(preview.get('scale')) in (int, float) and
                         math.isfinite(preview['scale']) and 0.05 <= preview['scale'] <= 8,
                         field + '.preview_state.scale', 'must be a valid zoom')
                _require(type(preview.get('fit')) is bool, field + '.preview_state.fit', 'must be boolean')
                _require(_nonnegative_integer(preview.get('page')), field + '.preview_state.page', 'must be nonnegative')
            if 'name' in item:
                _require(isinstance(item['name'], str), field + '.name', 'must be a string')
            _require(isinstance(item.get('text'), str), field + '.text', 'must be a string')
            _require(type(item.get('modified')) is bool, field + '.modified', 'must be a boolean')
            category = item.get('category_id')
            _require(isinstance(category, str) and category in category_ids,
                     field + '.category_id', 'must reference a category or be empty')
            _require(_nonnegative_integer(item.get('first_visible_line')),
                     field + '.first_visible_line', 'must be a nonnegative integer')
            _require('selection' in item, field + '.selection', 'is required')
            _validate_selection(item['selection'], field + '.selection')
            if 'preview_position' in item:
                position = item['preview_position']
                _require(type(position) in (int, float) and
                         (type(position) is int or math.isfinite(position)),
                         field + '.preview_position', 'must be a finite number')
    _require(state['active_id'] is None or state['active_id'] in document_ids,
             'active_id', 'must reference a document or be null')
    return state


def _reject_constant(value):
    raise ValueError(f'Invalid JSON constant {value}')


class WorkspaceStore:
    def __init__(self, path):
        self.path = Path(path)
        self._backup = self.path.with_name(self.path.name + '.bak')
        self._lock = Lock()
        self._closed = False
        self._pdf_asset_cache = {}
        self._verified_pdf_assets = {}
        self._sqlite = self.path.suffix.lower() in ('.sqlite3', '.sqlite', '.db')

    def load(self, *, readonly_pdf=False) -> dict | None:
        with self._lock:
            self._ensure_open()
            if self._sqlite:
                snapshot = self._sqlite_snapshot(readonly_pdf=readonly_pdf)
                if snapshot is None:
                    state = self._legacy_state(readonly_pdf=readonly_pdf)
                    if state is not None:
                        self._save_sqlite(state, None)
                    return state
                return snapshot
            snapshot = self._snapshot()
            state = snapshot[0] if snapshot is not None else None
            if readonly_pdf and state is not None:
                for document in state['documents']:
                    if document.get('kind') == 'pdf':
                        document.pop('image_edit', None)
                        document['modified'] = False
            return state

    def save(self, state) -> None:
        with self._lock:
            self._ensure_open()
            _validate(state)
            if self._sqlite:
                # Repair PDF sources still held in memory before validating disk snapshots.
                if any((item.get('image_edit') or {}).get('version') == 2
                       and 'data' in item['image_edit'].get('initial', {})
                       for item in state['documents']):
                    self._externalize_pdf_assets(state)
                try:
                    previous = self._sqlite_snapshot(resolve_assets=False)
                except _PdfAssetError:
                    # A closed PDF with a lost asset must not block the current valid workspace.
                    current = self._externalize_pdf_assets(state)
                    self._verify_pdf_assets(current)
                    self._replace_sqlite(self.path, current)
                    return
                if previous is None:
                    previous = self._legacy_state()
                self._save_sqlite(state, previous)
                return
            data = json.dumps(state, ensure_ascii=False, allow_nan=False,
                              separators=(',', ':')).encode('utf-8')
            previous = self._snapshot()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._replace(self._backup, previous[1] if previous is not None else data)
            self._replace(self.path, data)

    def _legacy_state(self, *, readonly_pdf=False):
        legacy = WorkspaceStore(self.path.with_suffix('.json'))
        try:
            return legacy.load(readonly_pdf=readonly_pdf)
        finally:
            legacy.close()

    def _sqlite_snapshot(self, resolve_assets=True, readonly_pdf=False):
        errors = []
        asset_errors_only = True
        for path in (self.path, self._backup):
            if not path.exists():
                continue
            try:
                with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
                    version = connection.execute('PRAGMA user_version').fetchone()[0]
                    if version > 1:
                        raise _FutureVersionError(f'{path}: Unsupported workspace schema version {version}')
                    if version != 1 or connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                        raise ValueError('Invalid SQLite workspace schema or integrity')
                    row = connection.execute('SELECT payload FROM workspace WHERE id=1').fetchone()
                    if row is None:
                        raise ValueError('Missing workspace snapshot')
                    state = _validate(json.loads(row[0], parse_constant=_reject_constant))
                    if readonly_pdf:
                        for document in state['documents']:
                            if document.get('kind') == 'pdf':
                                document.pop('image_edit', None)
                                document['modified'] = False
                    if resolve_assets:
                        return self._resolve_pdf_assets(state)
                    self._verify_pdf_assets(state)
                    return state
            except _FutureVersionError:
                raise
            except (sqlite3.Error, ValueError) as error:
                errors.append(f'{path}: {error}')
                asset_errors_only = asset_errors_only and isinstance(error, _PdfAssetError)
        if errors:
            error_type = _PdfAssetError if asset_errors_only else ValueError
            raise error_type('Cannot recover corrupt workspace cache: ' + '; '.join(errors))
        return None

    def _save_sqlite(self, state, previous):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 完整构建事务数据库再原子替换；损坏主库恢复时仍保留有效备份。
        self._replace_sqlite(self._backup, self._externalize_pdf_assets(previous if previous is not None else state, prune=False))
        self._replace_sqlite(self.path, self._externalize_pdf_assets(state))

    def _externalize_pdf_assets(self, state, prune=True):
        result = dict(state, documents=[])
        active_sources = {}
        for document in state['documents']:
            item = dict(document)
            edit = document.get('image_edit')
            if edit and edit.get('version') == 2 and 'data' in edit.get('initial', {}):
                source = edit['initial']['data']
                cached = self._pdf_asset_cache.get(id(source))
                if cached is not None and cached[0] is source:
                    digest = cached[1]
                else:
                    encoded = source.encode('ascii')
                    digest = hashlib.sha256(encoded).hexdigest()
                try:
                    self._verify_pdf_asset(digest)
                except ValueError:
                    folder = self.path.with_name(self.path.name + '.assets')
                    folder.mkdir(parents=True, exist_ok=True)
                    asset = folder / digest
                    self._replace(asset, source.encode('ascii'))
                    self._verified_pdf_assets[digest] = self._pdf_asset_signature(asset)
                active_sources[id(source)] = (source, digest)
                initial = dict(edit['initial'])
                del initial['data']
                initial['asset'] = digest
                item['image_edit'] = dict(edit, initial=initial)
            result['documents'].append(item)
        if prune:
            self._pdf_asset_cache = active_sources
        else:
            self._pdf_asset_cache.update(active_sources)
        return result

    def _resolve_pdf_assets(self, state):
        for document in state['documents']:
            edit = document.get('image_edit')
            if not edit or edit.get('version') != 2 or 'asset' not in edit.get('initial', {}):
                continue
            initial = edit['initial']
            digest = initial['asset']
            data = self._read_pdf_asset(digest)
            initial['data'] = data.decode('ascii')
            del initial['asset']
        return state

    def _pdf_asset_path(self, digest):
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise _PdfAssetError('Invalid PDF workspace asset')
        return self.path.with_name(self.path.name + '.assets') / digest

    @staticmethod
    def _pdf_asset_signature(path):
        info = path.stat()
        return info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino

    def _read_pdf_asset(self, digest):
        asset = self._pdf_asset_path(digest)
        try:
            signature = self._pdf_asset_signature(asset)
            data = asset.read_bytes()
            if signature != self._pdf_asset_signature(asset):
                raise _PdfAssetError('PDF workspace asset changed while reading')
        except OSError as error:
            raise _PdfAssetError('Missing PDF workspace asset') from error
        if hashlib.sha256(data).hexdigest() != digest:
            raise _PdfAssetError('Corrupt PDF workspace asset')
        self._verified_pdf_assets[digest] = signature
        return data

    def _verify_pdf_asset(self, digest):
        asset = self._pdf_asset_path(digest)
        try:
            signature = self._pdf_asset_signature(asset)
        except OSError as error:
            raise _PdfAssetError('Missing PDF workspace asset') from error
        if self._verified_pdf_assets.get(digest) != signature:
            self._read_pdf_asset(digest)

    def _verify_pdf_assets(self, state):
        for document in state['documents']:
            edit = document.get('image_edit')
            if edit and edit.get('version') == 2 and 'asset' in edit.get('initial', {}):
                self._verify_pdf_asset(edit['initial']['asset'])

    @staticmethod
    def _replace_sqlite(path, state):
        staging = path.with_name('.' + path.name + '.' + uuid4().hex + '.staging')
        try:
            data = json.dumps(state, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
            with closing(sqlite3.connect(staging)) as connection:
                connection.execute('PRAGMA synchronous=FULL')
                with connection:
                    connection.execute('CREATE TABLE workspace (id INTEGER PRIMARY KEY CHECK (id=1), payload TEXT NOT NULL)')
                    connection.execute('PRAGMA user_version=1')
                    connection.execute('INSERT INTO workspace VALUES (1, ?)', (data,))
                if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise OSError(f'Workspace cache verification failed: {path}')
            with staging.open('r+b') as stream:
                os.fsync(stream.fileno())
            os.replace(staging, path)
        except sqlite3.Error as error:
            raise OSError(f'{path}: {error}') from error
        finally:
            staging.unlink(missing_ok=True)
            staging.with_name(staging.name + '-journal').unlink(missing_ok=True)

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def _ensure_open(self):
        if self._closed:
            raise RuntimeError(f'Workspace store is closed: {self.path}')

    def _snapshot(self):
        errors = []
        corrupt = False
        for path in (self.path, self._backup):
            try:
                data = path.read_bytes()
            except FileNotFoundError:
                errors.append(f'{path}: missing')
                continue
            try:
                state = _validate(json.loads(data.decode('utf-8'), parse_constant=_reject_constant))
            except _FutureVersionError as error:
                raise _FutureVersionError(f'{path}: {error}') from error
            except (UnicodeError, ValueError) as error:
                corrupt = True
                errors.append(f'{path}: {error}')
                continue
            return state, data
        if corrupt:
            raise ValueError(f'Cannot recover corrupt workspace cache {self.path}: ' + '; '.join(errors))
        return None

    @staticmethod
    def _replace(path, data):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name,
                                             suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if temporary.read_bytes() != data:
                raise OSError(f'Workspace cache verification failed: {path}')
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
