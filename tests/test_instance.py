import json
from pathlib import Path
import subprocess
import sys
from time import monotonic

import pytest
from PySide6.QtCore import QObject
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def instances(app):
    created = []

    def create(path):
        from notepadview.instance import SingleInstance
        instance = SingleInstance(path)
        created.append(instance)
        return instance

    yield create
    for instance in reversed(created):
        instance.close()
    app.processEvents()


def wait_until(predicate):
    deadline = monotonic() + 3
    while not predicate():
        assert monotonic() < deadline, 'Local instance message did not arrive'
        QTest.qWait(10)


def test_exclusive_owner_forwards_unicode_paths_and_allows_takeover(instances, tmp_path):
    path = tmp_path / 'workspace.json'
    primary = instances(path)
    secondary = instances(path)
    assert isinstance(primary, QObject)
    assert primary.acquire()
    assert primary.acquire()
    assert Path(str(path) + '.lock').is_file()
    assert not secondary.acquire()
    received = []
    primary.filesReceived.connect(received.append)
    paths = [str(tmp_path / 'space in name.md'), str(tmp_path / '\u4e2d\u6587\u6587\u6863.txt')]
    assert secondary.forward(paths)
    wait_until(lambda: received == [paths])
    primary.close()
    primary.close()
    assert secondary.acquire()
    assert not primary.acquire()
    secondary.close()
    assert not Path(str(path) + '.lock').exists()


def test_forward_without_primary_returns_false(instances, tmp_path):
    assert not instances(tmp_path / 'missing.json').forward([])


@pytest.mark.parametrize('paths', [None, 'file.txt', [1], ['x' * (1024 * 1024)]])
def test_forward_rejects_invalid_or_oversized_message(instances, tmp_path, paths):
    primary = instances(tmp_path / 'workspace.json')
    assert primary.acquire()
    assert not instances(tmp_path / 'workspace.json').forward(paths)


def test_relative_and_absolute_paths_share_owner(instances, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    primary = instances('workspace.json')
    assert primary.acquire()
    assert not instances(tmp_path / 'workspace.json').acquire()


def test_empty_forward_requests_activation_without_releasing_primary(instances, tmp_path):
    primary = instances(tmp_path / 'workspace.json')
    secondary = instances(tmp_path / 'workspace.json')
    assert primary.acquire()
    received = []
    primary.filesReceived.connect(received.append)
    assert not secondary.acquire()
    assert secondary.forward([])
    wait_until(lambda: received == [[]])
    secondary.close()
    assert not secondary.acquire()
    assert primary.acquire()


def test_different_workspaces_are_independent_and_create_parent(instances, tmp_path):
    first = instances(tmp_path / 'new directory' / 'first.json')
    second = instances(tmp_path / 'new directory' / 'second.json')
    assert first.acquire()
    assert second.acquire()
    first_received, second_received = [], []
    first.filesReceived.connect(first_received.append)
    second.filesReceived.connect(second_received.append)
    assert instances(tmp_path / 'new directory' / 'second.json').forward(['second.txt'])
    wait_until(lambda: second_received == [['second.txt']])
    assert not first_received


def test_another_process_cannot_acquire_and_can_forward(instances, tmp_path):
    path = tmp_path / 'workspace.json'
    primary = instances(path)
    assert primary.acquire()
    received = []
    primary.filesReceived.connect(received.append)
    paths = ['a file.md', '\u4e2d\u6587.txt']
    program = '''
import json, sys
from PySide6.QtCore import QCoreApplication
from notepadview.instance import SingleInstance
app = QCoreApplication([])
instance = SingleInstance(sys.argv[1])
print(json.dumps([instance.acquire(), instance.forward(json.loads(sys.argv[2]))]))
instance.close()
'''
    process = subprocess.Popen([sys.executable, '-B', '-c', program, str(path), json.dumps(paths)],
                               cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    try:
        wait_until(lambda: process.poll() is not None)
        stdout, stderr = process.communicate(timeout=1)
        assert process.returncode == 0, stderr
        assert json.loads(stdout) == [False, True]
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=3)
    wait_until(lambda: received == [paths])
