import pytest


def test_environment_rejects_invalid_base_url():
    try:
        from notepadview.api_workbench.models import Environment
    except ModuleNotFoundError:
        pytest.fail('Environment model is not implemented')

    with pytest.raises(ValueError, match='base URL'):
        Environment(id='dev', name='Development', base_url='not a URL')


def test_environment_round_trips_headers_and_variables():
    try:
        from notepadview.api_workbench.models import Environment
    except ModuleNotFoundError:
        pytest.fail('Environment model is not implemented')

    original = Environment(
        id='dev',
        name='Development',
        base_url='https://dev.example.test/v1',
        default_headers={'Accept': 'application/json'},
        variables={'token': {'value': 'secret', 'secret': True}},
    )
    assert Environment.from_dict(original.to_dict()) == original


def test_collection_and_operation_round_trip():
    try:
        from notepadview.api_workbench.models import ApiCollection, ApiOperation
    except ImportError:
        pytest.fail('Collection and operation models are not implemented')

    operation = ApiOperation(
        id='get-users',
        method='GET',
        path='/users',
        summary='List users',
        tags=('Users',),
    )
    collection = ApiCollection(
        id='users-api',
        name='Users API',
        operations=[operation],
    )
    assert ApiCollection.from_dict(collection.to_dict()) == collection


def test_request_definition_rejects_unsupported_http_method():
    try:
        from notepadview.api_workbench.models import RequestDefinition
    except ImportError:
        pytest.fail('Request model is not implemented')

    with pytest.raises(ValueError, match='method'):
        RequestDefinition(id='bad', name='Bad', method='NOT A METHOD', url='https://example.test')


def test_history_round_trips_request_response_and_timestamp():
    try:
        from notepadview.api_workbench.models import RequestHistory
    except ImportError:
        pytest.fail('Request history model is not implemented')

    history = RequestHistory(
        id='history-1',
        completed_at='2026-10-25T10:00:00+00:00',
        request={'method': 'GET', 'url': 'https://example.test/health'},
        response={'status_code': 200, 'body': '{"ok":true}'},
    )
    assert RequestHistory.from_dict(history.to_dict()) == history


def test_collection_round_trips_editable_requests():
    from notepadview.api_workbench.models import ApiCollection, RequestDefinition

    request = RequestDefinition(
        id='create-user',
        name='Create user',
        method='POST',
        url='{{baseUrl}}/users',
        body_mode='raw',
        body='{"name":"Ada"}',
        pre_script="pm.environment.set('trace', '1');",
    )
    collection = ApiCollection(id='users', name='Users', requests=[request])

    assert ApiCollection.from_dict(collection.to_dict()).requests == [request]


def test_collection_and_request_round_trip_collection_variables_and_folder():
    from notepadview.api_workbench.models import ApiCollection, RequestDefinition

    request = RequestDefinition(
        id='get-pet', name='Get pet', folder='Pets', url='{{baseUrl}}/pets',
        cookies={'session': 'cookie-value'})
    collection = ApiCollection(
        id='pets', name='Pets',
        variables={'baseUrl': {'value': 'https://api.example.test', 'secret': False}},
        requests=[request],
    )

    restored = ApiCollection.from_dict(collection.to_dict())
    assert restored.variables == collection.variables
    assert restored.requests[0].folder == 'Pets'
    assert restored.requests[0].cookies == {'session': 'cookie-value'}
