from collections import defaultdict
from dataclasses import dataclass
import json
import re
from uuid import uuid4

import yaml
from yaml.constructor import ConstructorError

from notepadview.api_workbench.models import ApiCollection, ApiOperation


_HTTP_METHODS = (
    'get', 'put', 'post', 'delete', 'options', 'head', 'patch', 'trace',
)
_OPENAPI_VERSION = re.compile(r'^3\.(0|1)(?:\.\d+)?$')


class OpenApiImportError(ValueError):
    pass


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise ConstructorError(
                'while constructing a mapping', node.start_mark,
                'found an unhashable mapping key', key_node.start_mark) from error
        if duplicate:
            raise ConstructorError(
                'while constructing a mapping', node.start_mark,
                f'found duplicate key {key!r}', key_node.start_mark)
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


@dataclass
class ImportResult:
    collection: ApiCollection
    groups: dict[str, list[ApiOperation]]
    servers: list[str]


def _error(source_name, message):
    return OpenApiImportError(f'{source_name}: {message}')


def _parse_document(content, source_name):
    try:
        text = content.decode('utf-8-sig') if isinstance(content, bytes) else content
    except UnicodeDecodeError as error:
        raise _error(source_name, 'document must be UTF-8 encoded') from error
    if not isinstance(text, str):
        raise _error(source_name, 'document content must be text or bytes')
    try:
        if text.lstrip().startswith(('{', '[')):
            document = json.loads(text)
        else:
            document = yaml.load(text, Loader=_UniqueKeyLoader)
    except (json.JSONDecodeError, yaml.YAMLError) as error:
        mark = getattr(error, 'problem_mark', None)
        location = f' at line {mark.line + 1}, column {mark.column + 1}' if mark else ''
        raise _error(source_name, f'invalid JSON/YAML{location}: {error}') from error
    if not isinstance(document, dict):
        raise _error(source_name, 'document root must be an object')
    return document


def _resolve_pointer(root, reference, source_name):
    if not isinstance(reference, str) or not reference.startswith('#/'):
        raise _error(source_name, f'external or invalid $ref is not supported: {reference!r}')
    value = root
    for token in reference[2:].split('/'):
        token = token.replace('~1', '/').replace('~0', '~')
        if isinstance(value, list):
            try:
                value = value[int(token)]
            except (ValueError, IndexError) as error:
                raise _error(source_name, f'unresolved $ref {reference!r}') from error
        elif isinstance(value, dict) and token in value:
            value = value[token]
        else:
            raise _error(source_name, f'unresolved $ref {reference!r}')
    return value


def _resolve_refs(value, root, source_name, trail=()):
    if isinstance(value, dict):
        reference = value.get('$ref')
        if reference is not None:
            if reference in trail:
                return {'$ref': reference}
            target = _resolve_pointer(root, reference, source_name)
            if not isinstance(target, (dict, list)):
                return target
            resolved = _resolve_refs(target, root, source_name, (*trail, reference))
            if isinstance(resolved, dict):
                siblings = {key: item for key, item in value.items() if key != '$ref'}
                return {**resolved, **_resolve_refs(siblings, root, source_name, trail)}
            return resolved
        return {
            key: _resolve_refs(item, root, source_name, trail)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_resolve_refs(item, root, source_name, trail) for item in value]
    return value


def _servers(document, version):
    if version.startswith('3.'):
        servers = []
        for server in document.get('servers', []):
            if not isinstance(server, dict) or not isinstance(server.get('url'), str):
                continue
            url = server['url']
            variables = server.get('variables', {})
            if isinstance(variables, dict):
                for name, definition in variables.items():
                    if isinstance(definition, dict) and isinstance(definition.get('default'), str):
                        url = url.replace('{' + name + '}', definition['default'])
            servers.append(url)
        return servers
    host = document.get('host')
    if not isinstance(host, str) or not host:
        return []
    base_path = document.get('basePath', '')
    if not isinstance(base_path, str):
        base_path = ''
    schemes = document.get('schemes', [])
    scheme = 'https' if 'https' in schemes or not schemes else schemes[0]
    if scheme not in ('http', 'https'):
        return []
    return [f'{scheme}://{host}{base_path}']


def _normalize_swagger_body(parameters):
    body = next((parameter for parameter in parameters if parameter.get('in') == 'body'), None)
    if body is not None:
        return {'schema': body.get('schema', {}), 'required': body.get('required', False)}
    form_fields = [parameter for parameter in parameters if parameter.get('in') == 'formData']
    if form_fields:
        return {'form_fields': form_fields}
    return {}


def import_openapi(content, *, source_name='OpenAPI document'):
    document = _parse_document(content, source_name)
    swagger = document.get('swagger')
    version = document.get('openapi')
    if swagger == '2.0':
        version = '2.0'
    elif isinstance(version, str) and _OPENAPI_VERSION.fullmatch(version):
        pass
    else:
        raise _error(source_name, 'unsupported or missing Swagger/OpenAPI version')

    info = document.get('info')
    if not isinstance(info, dict):
        raise _error(source_name, 'info must be an object')
    paths = document.get('paths')
    if not isinstance(paths, dict):
        raise _error(source_name, 'paths must be an object')

    operations = []
    groups = defaultdict(list)
    for path, raw_path_item in paths.items():
        if not isinstance(path, str) or not path.startswith('/'):
            raise _error(source_name, f'invalid path key {path!r}')
        path_item = _resolve_refs(raw_path_item, document, source_name)
        if not isinstance(path_item, dict):
            raise _error(source_name, f'{path}: path item must be an object')
        path_parameters = path_item.get('parameters', [])
        if not isinstance(path_parameters, list):
            raise _error(source_name, f'{path}: parameters must be an array')
        for method, raw_operation in path_item.items():
            if method.casefold() not in _HTTP_METHODS:
                continue
            operation = _resolve_refs(raw_operation, document, source_name)
            if not isinstance(operation, dict):
                raise _error(source_name, f'{method.upper()} {path}: operation must be an object')
            parameters = operation.get('parameters', [])
            if not isinstance(parameters, list):
                raise _error(source_name, f'{method.upper()} {path}: parameters must be an array')
            resolved_parameters = [
                _resolve_refs(parameter, document, source_name)
                for parameter in (*path_parameters, *parameters)
            ]
            parameter_by_key = {}
            for parameter in resolved_parameters:
                if not isinstance(parameter, dict):
                    raise _error(source_name, f'{method.upper()} {path}: invalid parameter')
                key = (parameter.get('name'), parameter.get('in'))
                parameter_by_key[key] = parameter
            clean_parameters = [
                parameter for parameter in parameter_by_key.values()
                if parameter.get('in') in ('path', 'query', 'header')
            ]
            request_body = operation.get('requestBody', {})
            if version == '2.0':
                request_body = _normalize_swagger_body(list(parameter_by_key.values()))
            else:
                request_body = _resolve_refs(request_body, document, source_name)
            responses = _resolve_refs(operation.get('responses', {}), document, source_name)
            security = operation.get('security', document.get('security', []))
            security_definitions = (
                document.get('securityDefinitions', {}) if version == '2.0'
                else document.get('components', {}).get('securitySchemes', {})
            )
            auth = {
                name: security_definitions[name]
                for requirement in security if isinstance(requirement, dict)
                for name in requirement if name in security_definitions
            } if isinstance(security, list) else {}
            tags = operation.get('tags', [])
            if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
                raise _error(source_name, f'{method.upper()} {path}: tags must be strings')
            operation_id = operation.get('operationId')
            summary = operation.get('summary') or operation_id or f'{method.upper()} {path}'
            normalized = ApiOperation(
                id=uuid4().hex,
                method=method.upper(),
                path=path,
                summary=summary,
                tags=tuple(tags),
                parameters=tuple(clean_parameters),
                request_body=request_body if isinstance(request_body, dict) else {},
                responses=responses if isinstance(responses, dict) else {},
                auth=auth,
            )
            operations.append(normalized)
            for tag in tags or [f'Untagged: {path}']:
                groups[tag].append(normalized)

    if not operations:
        raise _error(source_name, 'document contains no HTTP operations')
    title = info.get('title')
    if not isinstance(title, str) or not title.strip():
        title = source_name
    description = info.get('description', '')
    if not isinstance(description, str):
        description = ''
    collection = ApiCollection(
        id=uuid4().hex,
        name=title,
        operations=operations,
        source=source_name,
        description=description,
    )
    return ImportResult(collection, dict(groups), _servers(document, version))
