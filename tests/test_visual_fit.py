import pytest
from PySide6.QtGui import QImage

from test_ui import app  # noqa: F401
from notepadview.image_editor import ImageEditor
from notepadview.pdf_editor import PdfEditor


@pytest.mark.parametrize('kind', ['png', 'pdf'])
def test_entering_visual_editor_fits_viewport(app, tmp_path, kind):
    path = tmp_path / f'large.{kind}'
    if kind == 'png':
        image = QImage(1600, 2400, QImage.Format_RGB32)
        image.fill(0xffffffff)
        assert image.save(str(path))
        editor = ImageEditor.from_path(path)
    else:
        import pymupdf
        with pymupdf.open() as document:
            document.new_page(width=1600, height=2400)
            document.save(str(path))
        editor = PdfEditor.from_path(path)
    try:
        editor.resize(1100, 750)
        editor.show()
        app.processEvents()
        for _ in range(2):
            assert editor._fitted
            bounds = editor.view.transform().mapRect(editor.scene.sceneRect())
            assert bounds.width() <= editor.view.viewport().width()
            assert bounds.height() <= editor.view.viewport().height()
            editor.zoom(2)
            assert not editor._fitted
            editor.hide()
            editor.show()
            app.processEvents()
    finally:
        editor.close()
