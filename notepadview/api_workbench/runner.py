from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode
import asyncio
import socket
import ssl
from contextlib import suppress
from threading import Event
from time import perf_counter

import httpx
from PySide6.QtCore import QObject, QRunnable, Signal

from notepadview.api_workbench.requests import PreparedRequest


@dataclass(frozen=True)
class TransportError:
    kind: str
    message: str


@dataclass
class ResponseSnapshot:
    status_code: int
    status: str
    elapsed_ms: float
    size_bytes: int
    headers: dict[str, str] = field(default_factory=dict)
    cookies: dict[str, str] = field(default_factory=dict)
    body: str = ''


@dataclass
class RequestResult:
    response: ResponseSnapshot | None = None
    transport_error: TransportError | None = None


class CancelToken:
    def __init__(self):
        self._event = Event()

    def cancel(self):
        self._event.set()

    @property
    def cancelled(self):
        return self._event.is_set()


def _transport_error(error):
    if isinstance(error, httpx.TimeoutException):
        return TransportError('timeout', 'The request timed out')
    elif isinstance(error, httpx.ConnectError):
        cause = error
        while cause is not None:
            if isinstance(cause, ssl.SSLError):
                return TransportError('tls', 'TLS negotiation or certificate verification failed')
            if isinstance(cause, socket.gaierror):
                return TransportError('dns', 'The target host could not be resolved')
            cause = cause.__cause__
        return TransportError('connection', 'Could not connect to the target server')
    elif isinstance(error, (httpx.UnsupportedProtocol, httpx.InvalidURL)):
        return TransportError('invalid_url', 'The request URL is invalid or uses an unsupported protocol')
    elif isinstance(error, OSError):
        return TransportError('local_file', str(error))
    return TransportError('network', 'The network request failed')


async def _wait_for_cancellation(cancel_token):
    while not cancel_token.cancelled:
        await asyncio.sleep(0.025)


async def _send_request_async(prepared_request, timeout, cancel_token):
    started = perf_counter()
    timeout_config = httpx.Timeout(
        float(timeout), connect=min(float(timeout), 10.0),
        read=float(timeout), write=float(timeout), pool=float(timeout),
    )

    async def perform_request():
        try:
            with ExitStack() as stack:
                files = []
                for name, path in prepared_request.files:
                    file_handle = stack.enter_context(open(path, 'rb'))
                    files.append((name, (Path(path).name, file_handle)))
                headers = dict(prepared_request.headers)
                content = prepared_request.content
                if prepared_request.body_mode == 'multipart' or files:
                    files = [(name, (None, value)) for name, value in prepared_request.data] + files
                    if not files:
                        from uuid import uuid4
                        boundary = uuid4().hex
                        content = f'--{boundary}--\r\n'.encode()
                        headers.setdefault('Content-Type', f'multipart/form-data; boundary={boundary}')
                elif prepared_request.data or prepared_request.body_mode == 'form':
                    content = urlencode(prepared_request.data).encode('utf-8')
                    headers.setdefault('Content-Type', 'application/x-www-form-urlencoded')
                async with httpx.AsyncClient(
                        timeout=timeout_config, verify=True, follow_redirects=True) as client:
                    async with client.stream(
                        prepared_request.method,
                        prepared_request.url,
                        params=prepared_request.params or None,
                        headers=headers or None,
                        cookies=prepared_request.cookies or None,
                        content=content,
                        files=files or None,
                    ) as response:
                        body_parts = []
                        size_bytes = 0
                        async for chunk in response.aiter_bytes():
                            body_parts.append(chunk)
                            size_bytes += len(chunk)
                        body_bytes = b''.join(body_parts)
                        elapsed_ms = (perf_counter() - started) * 1000
                        snapshot = ResponseSnapshot(
                            status_code=response.status_code,
                            status=response.reason_phrase,
                            elapsed_ms=elapsed_ms,
                            size_bytes=size_bytes,
                            headers=dict(response.headers),
                            cookies=dict(response.cookies),
                            body=body_bytes.decode(
                                response.encoding or 'utf-8', errors='replace'),
                        )
                        return RequestResult(response=snapshot)
        except (httpx.HTTPError, httpx.InvalidURL) as error:
            return RequestResult(transport_error=_transport_error(error))
        except OSError as error:
            return RequestResult(transport_error=_transport_error(error))

    request_task = asyncio.create_task(perform_request())
    cancel_task = asyncio.create_task(_wait_for_cancellation(cancel_token))
    done, _ = await asyncio.wait(
        (request_task, cancel_task), return_when=asyncio.FIRST_COMPLETED)
    if cancel_task in done:
        request_task.cancel()
        with suppress(asyncio.CancelledError):
            await request_task
        return RequestResult(transport_error=TransportError('cancelled', 'Request cancelled'))
    cancel_task.cancel()
    with suppress(asyncio.CancelledError):
        await cancel_task
    return await request_task


def send_request(prepared_request, *, timeout=30, cancel_token=None):
    if not isinstance(prepared_request, PreparedRequest):
        raise TypeError('prepared_request must be a PreparedRequest')
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError('Request timeout must be greater than zero')
    cancel_token = cancel_token or CancelToken()
    if cancel_token.cancelled:
        return RequestResult(transport_error=TransportError('cancelled', 'Request cancelled'))
    return asyncio.run(_send_request_async(prepared_request, timeout, cancel_token))


class _RequestSignals(QObject):
    finished = Signal(object)


class RequestWorker(QRunnable):
    def __init__(self, prepared_request, *, timeout=30, cancel_token=None):
        super().__init__()
        self.prepared_request = prepared_request
        self.timeout = timeout
        self.cancel_token = cancel_token or CancelToken()
        self.signals = _RequestSignals()

    def run(self):
        self.signals.finished.emit(send_request(
            self.prepared_request, timeout=self.timeout, cancel_token=self.cancel_token))
