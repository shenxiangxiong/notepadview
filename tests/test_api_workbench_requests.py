import base64

import pytest

from notepadview.api_workbench.models import Environment, RequestDefinition


def _prepare():
    try:
        from notepadview.api_workbench.requests import prepare_request
    except ImportError:
        pytest.fail('Request preparation is not implemented')
    return prepare_request


def test_prepare_request_expands_environment_and_request_overrides():
    prepare_request = _prepare()
    request = RequestDefinition(
        id='get-user', name='Get user',
        url='{{base}}/users/{{id}}?label={{label}}',
        variables={'id': {'value': '7'}},
        query=[{'key': 'active', 'value': '{{active}}'}],
    )
    environment = Environment(
        id='dev', name='Development', base_url='https://dev.example.test/v1',
        variables={
            'base': {'value': 'https://ignored.example.test'},
            'id': {'value': '1'},
            'label': {'value': 'first & user'},
            'active': {'value': 'true'},
        },
    )

    prepared = prepare_request(request, environment)

    assert prepared.url == 'https://dev.example.test/v1/users/7?label=first%20%26%20user'
    assert prepared.params == [('active', 'true')]


def test_prepare_request_rejects_unresolved_and_recursive_variables():
    prepare_request = _prepare()
    environment = Environment(id='dev', name='Dev', base_url='https://api.example.test')

    with pytest.raises(ValueError, match='missing'):
        prepare_request(RequestDefinition(id='missing', name='Missing', url='{{unknown}}'), environment)
    with pytest.raises(ValueError, match='cycle'):
        prepare_request(
            RequestDefinition(id='cycle', name='Cycle', url='https://example.test/{{a}}',
                              variables={'a': {'value': '{{b}}'}, 'b': {'value': '{{a}}'}}),
            environment,
        )


def test_prepare_request_merges_headers_and_applies_auth_and_cookies():
    prepare_request = _prepare()
    request = RequestDefinition(
        id='auth', name='Authenticated', method='POST', url='/private',
        headers={'x-mode': 'request'},
        auth={'type': 'basic', 'username': 'ada', 'password': 'secret'},
        cookies={'session': 'request-cookie'},
    )
    environment = Environment(
        id='dev', name='Dev', base_url='https://api.example.test',
        default_headers={'X-Mode': 'environment', 'Accept': 'application/json'},
    )

    prepared = prepare_request(
        request, environment,
        environment_cookies={'session': 'environment-cookie', 'theme': 'dark'})

    assert prepared.headers['x-mode'] == 'request'
    assert prepared.headers['Accept'] == 'application/json'
    assert prepared.headers['Authorization'] == 'Basic ' + base64.b64encode(b'ada:secret').decode()
    assert prepared.cookies == {'session': 'request-cookie', 'theme': 'dark'}


def test_prepare_request_encodes_form_bodies_and_api_key_query_auth():
    prepare_request = _prepare()
    request = RequestDefinition(
        id='search', name='Search', method='POST', url='/search',
        auth={'type': 'api_key', 'key': 'key', 'value': 'abc123', 'in': 'query'},
        body_mode='form', body='[{"key":"q","value":"hello world"}]',
    )
    environment = Environment(id='dev', name='Dev', base_url='https://api.example.test')

    prepared = prepare_request(request, environment)

    assert prepared.params == [('key', 'abc123')]
    assert prepared.data == [('q', 'hello world')]


def test_bearer_auth_uses_actual_token_in_transport_header():
    request = RequestDefinition(id='bearer', name='Bearer', url='https://example.test',
                                auth={'type': 'bearer', 'token': 'actual-token'})
    environment = Environment(id='local', name='Local', base_url='')
    assert _prepare()(request, environment).headers['Authorization'] == 'Bearer actual-token'
