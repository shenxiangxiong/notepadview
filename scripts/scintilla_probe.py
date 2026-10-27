"""Standalone native runtime probe, also built by PyInstaller for release checks."""
from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from pyside6_scintilla import Scintilla, ScintillaEdit, ScintillaDocument


def probe():
    app = QApplication.instance() or QApplication([])
    first, second = ScintillaEdit(), ScintillaEdit()
    first.setCodePage(65001)
    first.setText('alpha\nbeta\ngamma')
    shared = first.get_doc()
    second.set_doc(shared)
    assert bytes(second.getText(second.length() + 1)).rstrip(b'\0') == b'alpha\nbeta\ngamma'
    first.setMouseSelectionRectangularSwitch(True)
    first.setRectangularSelectionModifier(Scintilla.KeyMod.Alt)
    first.setVirtualSpaceOptions(Scintilla.VirtualSpace.RectangularSelection)
    first.resize(500, 300)
    first.show()
    app.processEvents()
    # Deliver native Qt mouse events with Alt retained throughout the drag.
    viewport = first.viewport()
    start = QPoint(first.pointXFromPosition(1), first.pointYFromPosition(1) + 5)
    end = QPoint(first.pointXFromPosition(11), first.pointYFromPosition(11) + 5)
    QTest.mousePress(viewport, Qt.LeftButton, Qt.AltModifier, start)
    QTest.mouseMove(viewport, end)
    QTest.mouseRelease(viewport, Qt.LeftButton, Qt.AltModifier, end)
    assert first.selectionIsRectangle()
    assert first.selections() >= 2
    # Each wrapper owns a native reference. Detach both views before releasing it.
    first.set_doc(ScintillaDocument())
    second.set_doc(ScintillaDocument())
    shared = None
    first.close()
    second.close()
    app.processEvents()
    print('Scintilla runtime, shared document, Alt-drag and release: PASS')


if __name__ == '__main__':
    probe()
