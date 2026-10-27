from notepadview.reader_jobs import ReaderJob
from test_ui import app  # noqa: F401


def test_reader_job_returns_operation_result(app):
    result = []
    job = ReaderJob('session-1', 3, lambda: PathResult('pdf'))
    job.signals.completed.connect(lambda *values: result.append(values))

    job.run()

    assert result == [('session-1', 3, PathResult('pdf'), None)]


def test_reader_job_surfaces_operation_error(app):
    result = []

    def fail():
        raise ValueError('bad document')

    job = ReaderJob('session-2', 4, fail)
    job.signals.completed.connect(lambda *values: result.append(values))
    job.run()

    assert result == [('session-2', 4, None, 'bad document')]


class PathResult:
    def __init__(self, value):
        self.value = value

    def __eq__(self, other):
        return isinstance(other, PathResult) and self.value == other.value
