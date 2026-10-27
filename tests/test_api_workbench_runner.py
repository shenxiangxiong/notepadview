import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from time import perf_counter, sleep

import pytest

from notepadview.api_workbench.models import Environment, RequestDefinition
from notepadview.api_workbench.requests import prepare_request


@pytest.fixture
def loopback_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/delay':
                from time import sleep as delay
                delay(1)
            if self.path.startswith('/status/'):
                self.send_response(int(self.path.rsplit('/', 1)[-1]))
                body = b'{"status":"returned"}'
            else:
                self.send_response(200)
                body = json.dumps({
                    'method': self.command,
                    'path': self.path,
                    'header': self.headers.get('X-Test'),
                    'body': '',
                }).encode()
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            size = int(self.headers.get('Content-Length', '0'))
            body = self.rfile.read(size).decode()
            response = json.dumps({
                'method': self.command, 'path': self.path,
                'header': self.headers.get('X-Test'), 'body': body,
            }).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *_):
            return

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _send():
    try:
        from notepadview.api_workbench.runner import send_request
    except ImportError:
        pytest.fail('HTTP request runner is not implemented')
    return send_request


def test_http_runner_sends_prepared_request_and_captures_response(loopback_server):
    send_request = _send()
    environment = Environment(id='local', name='Local', base_url=loopback_server)
    request = RequestDefinition(
        id='echo', name='Echo', method='POST', url='/echo?from=query',
        headers={'X-Test': 'header-value'}, body_mode='raw', body='request body',
    )

    result = send_request(prepare_request(request, environment), timeout=2)

    assert result.transport_error is None
    assert result.response.status_code == 200
    assert result.response.headers['content-type'] == 'application/json'
    assert json.loads(result.response.body) == {
        'method': 'POST', 'path': '/echo?from=query',
        'header': 'header-value', 'body': 'request body',
    }
    assert result.response.elapsed_ms >= 0


def test_http_500_is_returned_as_response_not_transport_error(loopback_server):
    send_request = _send()
    environment = Environment(id='local', name='Local', base_url=loopback_server)
    request = RequestDefinition(id='server-error', name='Server error', url='/status/500')

    result = send_request(prepare_request(request, environment), timeout=2)

    assert result.response.status_code == 500
    assert result.transport_error is None


def test_http_runner_cancels_a_request_before_server_response(loopback_server):
    from notepadview.api_workbench.runner import CancelToken

    send_request = _send()
    environment = Environment(id='local', name='Local', base_url=loopback_server)
    request = RequestDefinition(id='delay', name='Delay', url='/delay')
    token = CancelToken()
    started = Event()
    result = []

    def send():
        started.set()
        result.append(send_request(
            prepare_request(request, environment), timeout=3, cancel_token=token))

    thread = Thread(target=send)
    began = perf_counter()
    thread.start()
    assert started.wait(1)
    sleep(0.1)
    token.cancel()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert result[0].transport_error.kind == 'cancelled'
    assert perf_counter() - began < 0.8


@pytest.mark.parametrize('mode', ['form', 'multipart'])
def test_http_runner_sends_duplicate_form_fields(loopback_server, mode):
    request = RequestDefinition(id='form', name='Form', method='POST', url='/echo',
                                body_mode=mode, body='[{"key":"tag","value":"a"},{"key":"tag","value":"b"}]')
    result = _send()(prepare_request(request, Environment(id='local', name='Local', base_url=loopback_server)), timeout=2)
    body = json.loads(result.response.body)['body']
    if mode == 'form':
        assert body == 'tag=a&tag=b'
    else:
        assert body.count('name="tag"') == 2
        assert '\r\n\r\na\r\n' in body
