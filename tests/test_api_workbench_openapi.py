from pathlib import Path

import pytest

_FIXTURES = Path(__file__).parent / 'fixtures' / 'api_workbench'


def _import_openapi(content, source_name):
    try:
        from notepadview.api_workbench.openapi import import_openapi
    except ImportError:
        pytest.fail('OpenAPI importer is not implemented')
    return import_openapi(content, source_name=source_name)


@pytest.mark.parametrize(
    ('filename', 'expected_method', 'expected_path'),
    [
        ('openapi-2.json', 'GET', '/users'),
        ('openapi-3.0.yaml', 'GET', '/orders'),
        ('openapi-3.1.json', 'GET', '/health'),
    ],
)
def test_import_supports_swagger_and_openapi_versions(filename, expected_method, expected_path):
    path = _FIXTURES / filename
    result = _import_openapi(path.read_bytes(), source_name=filename)

    assert any(
        operation.method == expected_method and operation.path == expected_path
        for operation in result.collection.operations
    )


def test_import_groups_tagged_operations_then_untagged_by_path():
    path = _FIXTURES / 'openapi-3.0.yaml'
    result = _import_openapi(path.read_bytes(), source_name=path.name)

    assert [item.path for item in result.groups['Orders']] == ['/orders/{id}']
    assert [item.path for item in result.groups['Untagged: /orders']] == ['/orders']


def test_import_derives_server_url_from_openapi3_and_swagger2():
    openapi3 = _import_openapi(
        (_FIXTURES / 'openapi-3.0.yaml').read_bytes(), source_name='orders.yaml')
    swagger2 = _import_openapi(
        (_FIXTURES / 'openapi-2.json').read_bytes(), source_name='users.json')

    assert openapi3.servers == ['https://staging.api.example.test/v2']
    assert swagger2.servers == ['https://api.example.test/v1']


def test_import_rejects_unsupported_version_with_source_context():
    path = _FIXTURES / 'openapi-invalid.yaml'
    with pytest.raises(ValueError, match='openapi-invalid.yaml.*version'):
        _import_openapi(path.read_bytes(), source_name=path.name)


def test_swagger2_body_parameter_becomes_request_body():
    document = b'''{
      "swagger":"2.0","info":{"title":"Body API","version":"1"},
      "paths":{"/users":{"post":{
        "parameters":[{"name":"user","in":"body","required":true,
                       "schema":{"type":"object"}}],
        "responses":{"201":{"description":"Created"}}
      }}}
    }'''

    result = _import_openapi(document, source_name='body.json')

    assert result.collection.operations[0].request_body['schema']['type'] == 'object'


def test_swagger2_form_data_parameters_are_preserved():
    document = b'''{
      "swagger":"2.0","info":{"title":"Upload API","version":"1"},
      "paths":{"/upload":{"post":{
        "consumes":["multipart/form-data"],
        "parameters":[{"name":"file","in":"formData","type":"file","required":true}],
        "responses":{"200":{"description":"OK"}}
      }}}
    }'''

    result = _import_openapi(document, source_name='upload.json')

    assert result.collection.operations[0].request_body['form_fields'][0]['name'] == 'file'


def test_import_rejects_external_references_and_unsafe_yaml():
    external_ref = b'''{
      "openapi":"3.0.0","info":{"title":"Ref API","version":"1"},
      "paths":{"/users":{"get":{"parameters":[
        {"$ref":"https://example.test/parameters.yaml#/id"}
      ],"responses":{"200":{"description":"OK"}}}}}
    }'''
    with pytest.raises(ValueError, match='external'):
        _import_openapi(external_ref, source_name='external-ref.json')

    unsafe_yaml = b'''openapi: 3.0.0
info:
  title: Unsafe
  version: '1'
paths: !!python/object/apply:os.system ['whoami']
'''
    with pytest.raises(ValueError, match='invalid JSON/YAML'):
        _import_openapi(unsafe_yaml, source_name='unsafe.yaml')
