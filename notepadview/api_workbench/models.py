from dataclasses import dataclass, field
from datetime import datetime
import re
from urllib.parse import urlsplit


def _mapping(value, field_name):
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f'{field_name} must be an object with string keys')
    return value


def _variables(value, field_name):
    _mapping(value, field_name)
    for name, variable in value.items():
        if (not isinstance(name, str) or not name or not isinstance(variable, dict)
                or not isinstance(variable.get('value', ''), str)
                or type(variable.get('secret', False)) is not bool):
            raise ValueError(f'{field_name} entry {name!r} is invalid')


def _identifier(value, field_name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field_name} must not be empty')


@dataclass
class Environment:
    id: str
    name: str
    base_url: str
    default_headers: dict[str, str] = field(default_factory=dict)
    variables: dict[str, dict[str, object]] = field(default_factory=dict)

    def __post_init__(self):
        _identifier(self.id, 'Environment id')
        _identifier(self.name, 'Environment name')
        parsed = urlsplit(self.base_url)
        if self.base_url and (parsed.scheme not in ('http', 'https') or not parsed.hostname):
            raise ValueError('Environment base URL must be a valid HTTP or HTTPS URL')
        try:
            parsed.port
        except ValueError as error:
            raise ValueError('Environment base URL has an invalid port') from error
        if any(not isinstance(key, str) or not isinstance(value, str)
               for key, value in self.default_headers.items()):
            raise ValueError('Environment default headers must contain string names and values')
        _variables(self.variables, 'Environment variables')

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'base_url': self.base_url,
            'default_headers': dict(self.default_headers),
            'variables': {
                name: {'value': value.get('value', ''), 'secret': value.get('secret', False)}
                for name, value in self.variables.items()
            },
        }

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError('Environment must be an object')
        try:
            return cls(
                id=data['id'],
                name=data['name'],
                base_url=data['base_url'],
                default_headers=data.get('default_headers', {}),
                variables=data.get('variables', {}),
            )
        except KeyError as error:
            raise ValueError(f'Environment is missing {error.args[0]}') from error


@dataclass
class ApiOperation:
    id: str
    method: str
    path: str
    summary: str = ''
    tags: tuple[str, ...] = ()
    parameters: tuple[dict, ...] = ()
    request_body: dict = field(default_factory=dict)
    responses: dict = field(default_factory=dict)
    auth: dict = field(default_factory=dict)

    def __post_init__(self):
        _identifier(self.id, 'Operation id')
        if not isinstance(self.method, str) or not re.fullmatch(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+", self.method):
            raise ValueError('Operation method is invalid')
        if not isinstance(self.path, str) or not self.path.startswith('/'):
            raise ValueError('Operation path must begin with /')
        if not isinstance(self.summary, str):
            raise ValueError('Operation summary must be a string')
        if not isinstance(self.tags, (tuple, list)) or any(not isinstance(tag, str) for tag in self.tags):
            raise ValueError('Operation tags must be a sequence of strings')
        if not isinstance(self.parameters, (tuple, list)) or any(
                not isinstance(item, dict) for item in self.parameters):
            raise ValueError('Operation parameters must be a sequence of objects')
        _mapping(self.request_body, 'Operation request body')
        _mapping(self.responses, 'Operation responses')
        _mapping(self.auth, 'Operation auth')

    def to_dict(self):
        return {
            'id': self.id, 'method': self.method, 'path': self.path,
            'summary': self.summary, 'tags': list(self.tags),
            'parameters': [dict(item) for item in self.parameters],
            'request_body': dict(self.request_body), 'responses': dict(self.responses),
            'auth': dict(self.auth),
        }

    @classmethod
    def from_dict(cls, data):
        _mapping(data, 'Operation')
        try:
            return cls(
                id=data['id'], method=data['method'], path=data['path'],
                summary=data.get('summary', ''), tags=tuple(data.get('tags', ())),
                parameters=tuple(data.get('parameters', ())),
                request_body=data.get('request_body', {}),
                responses=data.get('responses', {}), auth=data.get('auth', {}),
            )
        except KeyError as error:
            raise ValueError(f'Operation is missing {error.args[0]}') from error


@dataclass
class ApiCollection:
    id: str
    name: str
    operations: list[ApiOperation] = field(default_factory=list)
    requests: list['RequestDefinition'] = field(default_factory=list)
    source: str = ''
    description: str = ''
    variables: dict[str, dict[str, object]] = field(default_factory=dict)

    def __post_init__(self):
        _identifier(self.id, 'Collection id')
        _identifier(self.name, 'Collection name')
        if not isinstance(self.operations, list) or any(
                not isinstance(operation, ApiOperation) for operation in self.operations):
            raise ValueError('Collection operations must be a list of API operations')
        if not isinstance(self.requests, list) or any(
                not isinstance(request, RequestDefinition) for request in self.requests):
            raise ValueError('Collection requests must be a list of request definitions')
        if not isinstance(self.source, str) or not isinstance(self.description, str):
            raise ValueError('Collection source and description must be strings')
        _variables(self.variables, 'Collection variables')

    def to_dict(self):
        return {
            'id': self.id, 'name': self.name,
            'operations': [operation.to_dict() for operation in self.operations],
            'requests': [request.to_dict() for request in self.requests],
            'source': self.source, 'description': self.description,
            'variables': {
                name: {'value': value.get('value', ''), 'secret': value.get('secret', False)}
                for name, value in self.variables.items()
            },
        }

    @classmethod
    def from_dict(cls, data):
        _mapping(data, 'Collection')
        try:
            return cls(
                id=data['id'], name=data['name'],
                operations=[ApiOperation.from_dict(item) for item in data.get('operations', [])],
                requests=[RequestDefinition.from_dict(item) for item in data.get('requests', [])],
                source=data.get('source', ''), description=data.get('description', ''),
                variables=data.get('variables', {}),
            )
        except KeyError as error:
            raise ValueError(f'Collection is missing {error.args[0]}') from error


@dataclass
class RequestDefinition:
    id: str
    name: str
    method: str = 'GET'
    url: str = ''
    query: list[dict[str, object]] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    auth: dict[str, object] = field(default_factory=dict)
    body_mode: str = 'none'
    body: str = ''
    pre_script: str = ''
    post_script: str = ''
    variables: dict[str, dict[str, object]] = field(default_factory=dict)
    folder: str = ''
    cookies: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        _identifier(self.id, 'Request id')
        _identifier(self.name, 'Request name')
        if not isinstance(self.method, str) or not re.fullmatch(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+", self.method):
            raise ValueError('Request method is invalid')
        if not isinstance(self.url, str):
            raise ValueError('Request URL must be a string')
        if not isinstance(self.folder, str):
            raise ValueError('Request folder must be a string')
        _mapping(self.headers, 'Request headers')
        _mapping(self.auth, 'Request auth')
        _mapping(self.cookies, 'Request cookies')
        if any(not isinstance(name, str) or not isinstance(value, str)
               for name, value in self.cookies.items()):
            raise ValueError('Request cookie names and values must be strings')
        _mapping(self.variables, 'Request variables')
        if self.body_mode not in ('none', 'raw', 'form', 'multipart', 'binary'):
            raise ValueError('Request body mode is invalid')
        if any(not isinstance(value, str) for value in
               (self.body, self.pre_script, self.post_script)):
            raise ValueError('Request body and scripts must be strings')

    def to_dict(self):
        return {
            'id': self.id, 'name': self.name, 'method': self.method, 'url': self.url,
            'query': [dict(item) for item in self.query],
            'headers': dict(self.headers), 'auth': dict(self.auth),
            'body_mode': self.body_mode, 'body': self.body,
            'pre_script': self.pre_script, 'post_script': self.post_script,
            'variables': {
                name: {'value': value.get('value', ''), 'secret': value.get('secret', False)}
                for name, value in self.variables.items()
            },
            'folder': self.folder,
            'cookies': dict(self.cookies),
        }

    @classmethod
    def from_dict(cls, data):
        _mapping(data, 'Request')
        try:
            return cls(
                id=data['id'], name=data['name'], method=data.get('method', 'GET'),
                url=data.get('url', ''), query=data.get('query', []),
                headers=data.get('headers', {}), auth=data.get('auth', {}),
                body_mode=data.get('body_mode', 'none'), body=data.get('body', ''),
                pre_script=data.get('pre_script', ''), post_script=data.get('post_script', ''),
                variables=data.get('variables', {}), folder=data.get('folder', ''),
                cookies=data.get('cookies', {}),
            )
        except KeyError as error:
            raise ValueError(f'Request is missing {error.args[0]}') from error


@dataclass
class RequestHistory:
    id: str
    completed_at: str
    request: dict
    response: dict

    def __post_init__(self):
        _identifier(self.id, 'History id')
        if not isinstance(self.completed_at, str):
            raise ValueError('History completion time must be an ISO datetime string')
        try:
            datetime.fromisoformat(self.completed_at.replace('Z', '+00:00'))
        except ValueError as error:
            raise ValueError('History completion time must be an ISO datetime string') from error
        _mapping(self.request, 'History request')
        _mapping(self.response, 'History response')

    def to_dict(self):
        return {
            'id': self.id, 'completed_at': self.completed_at,
            'request': dict(self.request), 'response': dict(self.response),
        }

    @classmethod
    def from_dict(cls, data):
        _mapping(data, 'History')
        try:
            return cls(
                id=data['id'], completed_at=data['completed_at'],
                request=data['request'], response=data['response'],
            )
        except KeyError as error:
            raise ValueError(f'History is missing {error.args[0]}') from error
