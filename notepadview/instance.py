"""One workspace owner, with newline-delimited JSON file-open requests."""
from hashlib import sha256
import json
import os
from pathlib import Path
from time import monotonic

from PySide6.QtCore import QCoreApplication, QIODevice, QLockFile, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


MAX_MESSAGE_BYTES = 1024 * 1024
IO_TIMEOUT_MS = 1500


class SingleInstance(QObject):
    filesReceived = Signal(list)

    def __init__(self, path):
        super().__init__()
        self._path = Path(os.path.normcase(str(Path(path).resolve())))
        digest = sha256(str(self._path).encode('utf-8')).hexdigest()
        self._name = 'notepad-view-' + digest
        self._lock = QLockFile(str(self._path) + '.lock')
        self._lock.setStaleLockTime(0)
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.UserAccessOption)
        self._server.newConnection.connect(self._accept_connections)
        self._clients = {}
        self._primary = False

    def acquire(self) -> bool:
        """Return True only while this object owns both the lock and listener."""
        if self._primary:
            return True
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
        if not self._lock.tryLock(0):
            return False
        # Only the lock owner may remove an endpoint left by a crashed process.
        QLocalServer.removeServer(self._name)
        if not self._server.listen(self._name):
            self._lock.unlock()
            return False
        self._primary = True
        return True

    def forward(self, paths: list[str]) -> bool:
        """Return True when the primary acknowledges a valid request."""
        if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
            return False
        try:
            payload = json.dumps(paths, ensure_ascii=False, separators=(',', ':')).encode('utf-8') + b'\n'
        except (UnicodeError, ValueError):
            return False
        if len(payload) > MAX_MESSAGE_BYTES:
            return False
        socket = QLocalSocket()
        try:
            socket.connectToServer(self._name, QIODevice.ReadWrite)
            if not socket.waitForConnected(IO_TIMEOUT_MS):
                return False
            if socket.write(payload) != len(payload):
                return False
            socket.flush()
            deadline = monotonic() + IO_TIMEOUT_MS / 1000
            response = bytearray()
            while monotonic() < deadline:
                QCoreApplication.processEvents()
                response.extend(bytes(socket.readAll()))
                if b'\n' in response:
                    return response == b'ok\n'
                if socket.state() == QLocalSocket.UnconnectedState:
                    return False
                socket.waitForReadyRead(10)
            return False
        finally:
            socket.close()

    def _accept_connections(self):
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            socket.setReadBufferSize(MAX_MESSAGE_BYTES + 1)
            self._clients[socket] = bytearray()
            socket.readyRead.connect(lambda peer=socket: self._read_request(peer))
            socket.disconnected.connect(lambda peer=socket: self._discard_client(peer))
            self._read_request(socket)

    def _read_request(self, socket):
        buffer = self._clients.get(socket)
        if buffer is None:
            return
        buffer.extend(bytes(socket.read(MAX_MESSAGE_BYTES + 1 - len(buffer))))
        if len(buffer) > MAX_MESSAGE_BYTES:
            socket.abort()
            return
        if b'\n' not in buffer:
            return
        line = bytes(buffer).split(b'\n', 1)[0]
        try:
            paths = json.loads(line.decode('utf-8'))
        except (UnicodeError, ValueError):
            socket.abort()
            return
        if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
            socket.abort()
            return
        self._clients.pop(socket, None)
        socket.write(b'ok\n')
        socket.flush()
        self.filesReceived.emit(paths)
        socket.disconnectFromServer()

    def _discard_client(self, socket):
        self._clients.pop(socket, None)
        socket.deleteLater()

    def close(self):
        """Release owned connections, listener and lock. Safe to call repeatedly."""
        for socket in list(self._clients):
            socket.abort()
        self._clients.clear()
        if self._primary:
            self._server.close()
            self._lock.unlock()
            self._primary = False
