import pytest


def _script_runner(timeout=0.5):
    try:
        from notepadview.api_workbench.scripts import ScriptRunner
    except ImportError:
        pytest.fail('API script runner is not implemented')
    return ScriptRunner(timeout_seconds=timeout)


def test_response_script_asserts_and_sets_environment_variable():
    runner = _script_runner()
    result = runner.run_after(
        "pm.test('created', () => pm.expect(pm.response.code).to.eql(201));"
        "pm.environment.set('userId', pm.response.json().id);",
        response={'status_code': 201, 'body': '{"id":"42"}', 'headers': {'Content-Type': 'application/json'}},
        environment={},
    )

    assert result.tests[0].passed
    assert result.environment_updates == {'userId': '42'}
    assert result.error is None


def test_request_script_updates_variables_and_reports_assertion_errors():
    runner = _script_runner()
    result = runner.run_before(
        "pm.variables.set('trace', 'abc');"
        "pm.test('missing', () => pm.expect(pm.request.method).to.eql('POST'));",
        request={'method': 'GET', 'url': 'https://example.test'},
        environment={},
    )

    assert result.variable_updates == {'trace': 'abc'}
    assert result.tests[0].passed is False
    assert 'POST' in result.tests[0].message


def test_script_runner_has_no_host_capabilities_and_enforces_timeout():
    runner = _script_runner(timeout=0.1)
    safe_result = runner.run_before(
        "pm.test('sandbox', () => pm.expect(typeof require).to.eql('undefined'));",
        request={}, environment={},
    )
    assert safe_result.tests[0].passed

    timed_out = runner.run_before('while (true) {}', request={}, environment={})
    assert timed_out.timed_out is True
    assert timed_out.error
