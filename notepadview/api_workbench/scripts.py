from dataclasses import dataclass, field
import json
import multiprocessing

from PySide6.QtCore import QCoreApplication
from PySide6.QtQml import QJSEngine


@dataclass(frozen=True)
class ScriptTestResult:
    name: str
    passed: bool
    message: str = ''


@dataclass
class ScriptResult:
    tests: list[ScriptTestResult] = field(default_factory=list)
    environment_updates: dict[str, str] = field(default_factory=dict)
    variable_updates: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    timed_out: bool = False


def _json_literal(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


_BOOTSTRAP = r"""
var __apiResult = {
  tests: [],
  environmentUpdates: {},
  variableUpdates: {}
};
var __environment = JSON.parse(__environmentJson);
var __variables = JSON.parse(__variablesJson);
var __request = JSON.parse(__requestJson);
var __response = JSON.parse(__responseJson);
function __equal(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}
function __expect(actual) {
  return {to: {
    eql: function(expected) {
      if (!__equal(actual, expected)) {
        throw new Error('Expected ' + JSON.stringify(actual) + ' to deeply equal ' + JSON.stringify(expected));
      }
    },
    equal: function(expected) {
      if (actual !== expected) {
        throw new Error('Expected ' + JSON.stringify(actual) + ' to equal ' + JSON.stringify(expected));
      }
    },
    include: function(expected) {
      if (typeof actual !== 'string' || actual.indexOf(expected) < 0) {
        throw new Error('Expected ' + JSON.stringify(actual) + ' to include ' + JSON.stringify(expected));
      }
    }
  }};
}
var pm = {
  request: __request,
  response: {
    code: __response.status_code || 0,
    status: __response.status || '',
    headers: __response.headers || {},
    text: function() { return __response.body || ''; },
    json: function() { return JSON.parse(__response.body || 'null'); }
  },
  environment: {
    get: function(name) {
      return Object.prototype.hasOwnProperty.call(__apiResult.environmentUpdates, name)
        ? __apiResult.environmentUpdates[name] : __environment[name];
    },
    set: function(name, value) {
      __apiResult.environmentUpdates[String(name)] = String(value);
    }
  },
  variables: {
    get: function(name) {
      if (Object.prototype.hasOwnProperty.call(__apiResult.variableUpdates, name)) {
        return __apiResult.variableUpdates[name];
      }
      if (Object.prototype.hasOwnProperty.call(__variables, name)) {
        return __variables[name];
      }
      return pm.environment.get(name);
    },
    set: function(name, value) {
      __apiResult.variableUpdates[String(name)] = String(value);
    }
  },
  expect: __expect,
  test: function(name, callback) {
    try {
      callback();
      __apiResult.tests.push({name: String(name), passed: true, message: ''});
    } catch (error) {
      __apiResult.tests.push({
        name: String(name), passed: false,
        message: error && error.message ? String(error.message) : String(error)
      });
    }
  }
};
"""


class ScriptRunner:
    def __init__(self, timeout_seconds=1.0):
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError('Script timeout must be greater than zero')
        self.timeout_seconds = float(timeout_seconds)

    def run_before(self, script, *, request, environment, variables=None):
        return self._run(
            script, request=request, environment=environment,
            variables=variables or {}, response={},
        )

    def run_after(self, script, *, response, environment, variables=None, request=None):
        return self._run(
            script, request=request or {}, environment=environment,
            variables=variables or {}, response=response,
        )

    def _run(self, script, *, request, environment, variables, response):
        if not isinstance(script, str):
            raise TypeError('Script must be text')
        if any(not isinstance(item, dict) for item in (request, environment, variables, response)):
            raise TypeError('Script request, response, and variables must be objects')
        context = multiprocessing.get_context('spawn')
        parent, child = context.Pipe()
        process = context.Process(
            target=_run_script_process,
            args=(child, script, request, environment, variables, response),
        )
        process.start()
        child.close()
        if not parent.poll(15):
            process.terminate()
            process.join()
            parent.close()
            return ScriptResult(error='JavaScript worker did not initialize')
        ready = parent.recv()
        if not ready.get('ready'):
            process.join()
            parent.close()
            return ScriptResult(error=ready.get('error', 'JavaScript worker failed to initialize'))
        if not parent.poll(self.timeout_seconds):
            process.terminate()
            process.join()
            parent.close()
            return ScriptResult(
                error=f'JavaScript exceeded the {self.timeout_seconds:g}s execution limit',
                timed_out=True,
            )
        output = parent.recv()
        process.join()
        parent.close()
        if process.exitcode != 0:
            return ScriptResult(error='JavaScript worker exited unexpectedly')
        if output.get('error'):
            return ScriptResult(error=output['error'])
        tests = [
            ScriptTestResult(
                name=item.get('name', ''),
                passed=bool(item.get('passed', False)),
                message=item.get('message', ''),
            )
            for item in output.get('tests', [])
        ]
        return ScriptResult(
            tests=tests,
            environment_updates=output.get('environmentUpdates', {}),
            variable_updates=output.get('variableUpdates', {}),
        )


def _run_script_process(connection, script, request, environment, variables, response):
    application = QCoreApplication([])
    engine = QJSEngine()
    engine.globalObject().setProperty('__environmentJson', _json_literal(environment))
    engine.globalObject().setProperty('__variablesJson', _json_literal(variables))
    engine.globalObject().setProperty('__requestJson', _json_literal(request))
    engine.globalObject().setProperty('__responseJson', _json_literal(response))
    setup_result = engine.evaluate(_BOOTSTRAP)
    if setup_result.isError():
        connection.send({'ready': False, 'error': setup_result.toString()})
        connection.close()
        return
    connection.send({'ready': True})
    result = engine.evaluate(script)
    if result.isError():
        connection.send({'error': result.toString()})
    else:
        output = engine.globalObject().property('__apiResult').toVariant()
        connection.send(output)
    connection.close()
    del application
