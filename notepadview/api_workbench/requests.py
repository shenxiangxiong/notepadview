from dataclasses import dataclass, field
import base64
import json
import re
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from notepadview.api_workbench.models import Environment, RequestDefinition


_VARIABLE = re.compile(r'\{\{([^{}]+)\}\}')


@dataclass
class PreparedRequest:
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    params: list[tuple[str, str]] = field(default_factory=list)
    cookies: dict[str, str] = field(default_factory=dict)
    content: bytes | None = None
    data: list[tuple[str, str]] = field(default_factory=list)
    files: list[tuple[str, str]] = field(default_factory=list)
    body_mode: str = 'none'


class VariableResolutionError(ValueError):
    pass


def _variable_values(environment, collection_variables, request):
    variables = {
        name: item.get('value', '')
        for name, item in environment.variables.items()
    }
    variables.update({
        name: item.get('value', '')
        for name, item in (collection_variables or {}).items()
    })
    variables.update({
        'base': environment.base_url.rstrip('/'),
        'baseUrl': environment.base_url.rstrip('/'),
    })
    variables.update({
        name: item.get('value', '')
        for name, item in request.variables.items()
    })
    return variables


def _expand(value, variables, *, encode=False):
    cache = {}

    def resolve(name, stack):
        if name not in variables:
            raise VariableResolutionError(f'missing or unresolved variable {name!r}')
        if name in stack:
            raise VariableResolutionError(f'Variable reference cycle: {" -> ".join((*stack, name))}')
        if name not in cache:
            raw = variables[name]
            if not isinstance(raw, str):
                raise VariableResolutionError(f'Variable {name!r} must contain text')
            cache[name] = _VARIABLE.sub(
                lambda match: resolve(match.group(1), (*stack, name)), raw)
        return cache[name]

    def substitute(match):
        name = match.group(1)
        resolved = resolve(name, ())
        if encode and name not in ('base', 'baseUrl'):
            return quote(resolved, safe='-._~')
        return resolved

    expanded = _VARIABLE.sub(substitute, value)
    return quote(expanded, safe=':/?#[]@!$&\'()*+,;=%-._~') if encode else expanded


def _merge_headers(environment_headers, request_headers, variables):
    merged = {}
    normalized_names = {}
    for source in (environment_headers, request_headers):
        for name, value in source.items():
            if not isinstance(name, str) or not isinstance(value, str):
                raise ValueError('Request header names and values must be strings')
            normalized = name.casefold()
            previous = normalized_names.get(normalized)
            if previous is not None:
                merged.pop(previous, None)
            normalized_names[normalized] = name
            merged[name] = _expand(value, variables)
    return merged


def _query_params(query, variables):
    params = []
    for item in query:
        if not isinstance(item, dict):
            raise ValueError('Query parameters must be objects')
        if item.get('disabled', False):
            continue
        key = item.get('key', '')
        value = item.get('value', '')
        if not isinstance(key, str) or not isinstance(value, (str, int, float)):
            raise ValueError('Query parameter keys and values must be text or numbers')
        params.append((_expand(key, variables), _expand(str(value), variables)))
    return params


def _form_values(body, variables):
    try:
        decoded = json.loads(body)
    except json.JSONDecodeError as error:
        raise ValueError('Form request body must be a JSON object or array') from error
    if isinstance(decoded, dict):
        entries = list(decoded.items())
    elif isinstance(decoded, list):
        entries = []
        for item in decoded:
            if not isinstance(item, dict) or not isinstance(item.get('key'), str):
                raise ValueError('Form fields must contain string keys')
            if item.get('disabled', False):
                continue
            entries.append((item['key'], item.get('value', '')))
    else:
        raise ValueError('Form request body must be a JSON object or array')
    result = []
    for key, value in entries:
        if not isinstance(key, str) or not isinstance(value, (str, int, float, bool)):
            raise ValueError('Form field names and values must be scalar text')
        result.append((_expand(key, variables), _expand(str(value), variables)))
    return result


def _multipart_values(body, variables):
    try:
        entries = json.loads(body) if body else []
    except json.JSONDecodeError as error:
        raise ValueError('Multipart body must be a JSON array') from error
    if not isinstance(entries, list):
        raise ValueError('Multipart body must be a JSON array')
    data, files = [], []
    for item in entries:
        if not isinstance(item, dict) or not isinstance(item.get('key'), str):
            raise ValueError('Multipart fields must contain string keys')
        if item.get('disabled', False):
            continue
        key = _expand(item['key'], variables)
        if item.get('type') == 'file':
            src = item.get('src', '')
            if isinstance(src, list):
                files.extend((key, _expand(str(path), variables)) for path in src)
            elif isinstance(src, str) and src:
                files.append((key, _expand(src, variables)))
            else:
                raise ValueError(f'Multipart file field {key!r} has no path')
        else:
            value = item.get('value', '')
            if not isinstance(value, (str, int, float, bool)):
                raise ValueError(f'Multipart field {key!r} must contain scalar text')
            data.append((key, _expand(str(value), variables)))
    return data, files


def _apply_auth(headers, params, auth, variables):
    auth_type = auth.get('type', 'none')
    if auth_type in ('', 'none', None):
        return params
    if auth_type == 'bearer':
        token = _expand(str(auth.get('token', '')), variables)
        if not token:
            raise ValueError('Bearer token is empty')
        headers['Authorization'] = f'Bearer {token}'
    elif auth_type == 'basic':
        username = _expand(str(auth.get('username', '')), variables)
        password = _expand(str(auth.get('password', '')), variables)
        encoded = base64.b64encode(f'{username}:{password}'.encode('utf-8')).decode('ascii')
        headers['Authorization'] = f'Basic {encoded}'
    elif auth_type == 'api_key':
        key = _expand(str(auth.get('key', '')), variables)
        value = _expand(str(auth.get('value', '')), variables)
        location = auth.get('in', 'header')
        if not key or not value:
            raise ValueError('API key name and value are required')
        if location == 'header':
            headers[key] = value
        elif location == 'query':
            params.append((key, value))
        else:
            raise ValueError('API key location must be header or query')
    else:
        raise ValueError(f'Unsupported authentication type: {auth_type}')
    return params


def prepare_request(request, environment, *, cookies=None, environment_cookies=None,
                    collection_variables=None):
    if not isinstance(request, RequestDefinition):
        raise TypeError('request must be a RequestDefinition')
    if not isinstance(environment, Environment):
        raise TypeError('environment must be an Environment')
    variables = _variable_values(environment, collection_variables, request)
    expanded_url = _expand(request.url, variables, encode=True)
    parsed = urlsplit(expanded_url)
    if parsed.scheme:
        url = expanded_url
    else:
        url = urljoin(environment.base_url.rstrip('/') + '/', expanded_url.lstrip('/'))
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise ValueError('Request URL must be a valid HTTP or HTTPS URL')
    try:
        parsed.port
    except ValueError as error:
        raise ValueError('Request URL has an invalid port') from error

    headers = _merge_headers(environment.default_headers, request.headers, variables)
    params = _query_params(request.query, variables)
    params = _apply_auth(headers, params, request.auth, variables)
    body = _expand(request.body, variables) if request.body else ''
    content = None
    data = []
    files = []
    if request.body_mode == 'raw':
        content = body.encode('utf-8')
    elif request.body_mode == 'form':
        data = _form_values(body, variables)
    elif request.body_mode == 'multipart':
        data, files = _multipart_values(body, variables)
    elif request.body_mode == 'binary':
        file_path = Path(body)
        try:
            content = file_path.read_bytes()
        except OSError as error:
            raise ValueError(f'Unable to read binary request body {file_path}: {error}') from error
    elif request.body_mode != 'none':
        raise ValueError(f'Unsupported request body mode: {request.body_mode}')

    prepared_cookies = {}
    all_cookies = dict(environment_cookies or {})
    all_cookies.update(request.cookies)
    all_cookies.update(cookies or {})
    for name, value in all_cookies.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise ValueError('Cookie names and values must be strings')
        prepared_cookies[_expand(name, variables)] = _expand(value, variables)
    return PreparedRequest(
        method=request.method.upper(),
        url=urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, parsed.fragment)),
        headers=headers,
        params=params,
        cookies=prepared_cookies,
        content=content,
        data=data,
        files=files,
        body_mode=request.body_mode,
    )
