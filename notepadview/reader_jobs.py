"""Qt worker for reader parsing and external format conversions."""
import subprocess

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class ReaderJobSignals(QObject):
    completed = Signal(str, int, object, object)


class ReaderJob(QRunnable):
    def __init__(self, session_id, generation, operation):
        super().__init__()
        self.session_id = session_id
        self.generation = generation
        self.operation = operation
        self.signals = ReaderJobSignals()

    @Slot()
    def run(self):
        try:
            result = self.operation()
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            self.signals.completed.emit(self.session_id, self.generation, None, str(error))
        else:
            self.signals.completed.emit(self.session_id, self.generation, result, None)
