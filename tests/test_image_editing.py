import xml.etree.ElementTree as ET

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QMessageBox, QPushButton

from test_ui import app, window  # noqa: F401


def svg_file(tmp_path):
    path = tmp_path / 'drawing.svg'
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" viewBox="0 0 200 100"><g transform="translate(20 10)"><rect width="60" height="40" style="fill:red;stroke:black"/></g><text x="100" y="60">Hello</text></svg>', encoding='utf-8')
    return path


def png_file(tmp_path):
    path = tmp_path / 'photo.png'
    image = QImage(200, 100, QImage.Format_ARGB32)
    image.fill(Qt.white)
    assert image.save(str(path), 'PNG')
    return path


def exported(editor, path):
    editor.save(path)
    return QImage(str(path)).convertToFormat(QImage.Format_ARGB32)


@pytest.mark.parametrize('suffix', ['png', 'jpg'])
@pytest.mark.parametrize('horizontal', [True, False])
def test_flip_raster_glyphs_history_and_original(app, tmp_path, suffix, horizontal):
    from notepadview.image_editor import ImageEditor
    path = tmp_path / f'asymmetric.{suffix}'
    image = QImage(200, 100, QImage.Format_ARGB32)
    image.fill(Qt.white)
    image.setPixelColor(5, 7, Qt.red)
    assert image.save(str(path))
    editor = ImageEditor.from_path(path)
    editor.add_text('Flip R', x=17, y=13)
    editor.set_style(font_family='Arial', font_size=27, fill='#123456')
    original = exported(editor, tmp_path / 'before.png')
    raster = editor._state['raster']
    editor.zoom(1.25)
    zoom = editor.view.transform()
    editor.flip_image(horizontal)
    flipped = exported(editor, tmp_path / 'after.png')
    assert flipped == original.flipped(Qt.Horizontal if horizontal else Qt.Vertical)
    assert editor.view.transform() == zoom
    assert editor._state['raster'] == raster
    restored = ImageEditor.from_snapshot(editor.snapshot())
    assert exported(restored, tmp_path / 'restored.png') == flipped
    restored.undo_stack.undo()
    assert exported(restored, tmp_path / 'undo.png') == original
    restored.undo_stack.redo()
    assert exported(restored, tmp_path / 'redo.png') == flipped
    editor.flip_image(horizontal)
    assert exported(editor, tmp_path / 'twice.png') == original
    editor.resize_image(400, 200)
    resized = exported(editor, tmp_path / 'resized.png')
    editor.flip_image(horizontal)
    assert exported(editor, tmp_path / 'resized-flip.png') == resized.flipped(Qt.Horizontal if horizontal else Qt.Vertical)
    editor.add_text('New', x=10, y=12)
    assert editor.layers[-1]['x'] == 10 and editor.layers[-1]['y'] == 12
    from notepadview.image_editor import TextItem
    added = next(item for item in editor.scene.items()
                 if isinstance(item, TextItem) and item.index == len(editor.layers) - 1)
    assert added.transform().isIdentity()
    editor.move_text(25, 30)
    editor.set_style(fill='#ff0000')
    assert editor.layers[-1]['x'] == 25 and editor.layers[-1]['fill'] == '#ff0000'
    editor.flip_image(not horizontal)
    assert exported(editor, tmp_path / 'both.png') == exported(
        ImageEditor.from_snapshot(editor.snapshot()), tmp_path / 'both-restored.png')
    labels = [button.text() for button in editor.findChildren(QPushButton)]
    assert labels.index('水平翻转') < labels.index('垂直翻转') < labels.index('缩小')


@pytest.mark.parametrize('horizontal', [True, False])
def test_svg_flip_preserves_defs_viewbox_selection_and_reopen(app, tmp_path, horizontal):
    from notepadview.image_editor import ImageEditor
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QPainter

    def render(editor):
        image = QImage(*editor.image_size, QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        editor._renderer().render(painter, QRectF(0, 0, *editor.image_size))
        painter.end()
        return image

    path = tmp_path / 'offset.svg'
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" viewBox="10 20 200 100"><defs><linearGradient id="paint"><stop stop-color="red"/><stop offset="1" stop-color="blue"/></linearGradient></defs><g transform="translate(10 20)"><rect id="box" x="10" y="10" width="30" height="20" fill="url(#paint)"/><text x="60" y="40">Flip R</text></g></svg>', encoding='utf-8')
    editor = ImageEditor.from_path(path)
    before = render(editor)
    defs = ET.tostring(editor.svg_root.find('{*}defs'))
    editor.flip_image(horizontal)
    after = render(editor)
    # SVG 字体栅格化允许边缘抗锯齿差异，图形位置与字形应整体镜像。
    expected = before.flipped(Qt.Horizontal if horizontal else Qt.Vertical)
    error = sum(abs(after.pixelColor(x, y).alpha() - expected.pixelColor(x, y).alpha())
                for x in range(200) for y in range(100))
    assert error < 15000
    assert after != before
    assert editor.svg_root.get('viewBox') == '10 20 200 100'
    assert ET.tostring(editor.svg_root.find('{*}defs')) == defs
    assert editor.select_at(QPointF(175 if horizontal else 25, 20 if horizontal else 80))
    assert editor.selected == 'box'
    editor.set_style(fill='#00ff00')
    saved = tmp_path / 'flipped.svg'
    editor.save(saved)
    reopened = ImageEditor.from_path(saved)
    assert render(reopened) == render(editor)
    reopened.flip_image(horizontal)
    reopened.flip_image(horizontal)
    assert render(reopened) == render(editor)
    editor.undo_stack.undo()
    editor.flip_image(horizontal)
    assert render(editor) == before
    editor.undo_stack.undo()
    editor.resize_image(400, 200)
    assert editor.select_at(QPointF(350 if horizontal else 50, 40 if horizontal else 160))
    restored = ImageEditor.from_snapshot(editor.snapshot())
    assert render(restored) == render(editor)


def test_svg_select_style_resize_undo_and_save(app, tmp_path):
    from notepadview.image_editor import ImageEditor
    path = svg_file(tmp_path)
    editor = ImageEditor.from_path(path)
    assert editor.select_at(QPointF(30, 20))
    editor.set_style(fill='#123456', stroke='#abcdef')
    editor.resize_image(400, 200)
    assert editor.image_size == (400, 200)
    editor.undo_stack.undo()
    assert editor.image_size == (200, 100)
    editor.undo_stack.redo()
    destination = tmp_path / 'edited.svg'
    editor.save(destination)
    root = ET.fromstring(destination.read_bytes())
    rect = root.find('.//{*}rect')
    assert rect.get('id')
    assert '#123456' in rect.get('style')
    assert '#abcdef' in rect.get('style')
    assert root.get('width') == '400'
    assert not editor.undo_stack.isClean()  # 写文件与提交保存点分开
    assert b'<svg' in destination.read_bytes() or b':svg' in destination.read_bytes()


def test_svg_font_and_secure_xml(app, tmp_path):
    from notepadview.image_editor import ImageEditor
    editor = ImageEditor.from_path(svg_file(tmp_path))
    text_id = next(node.get('id') for node in editor.svg_root.iter() if node.tag.endswith('text'))
    editor.select_element(text_id)
    editor.set_style(font_family='Arial', font_size=28)
    text = next(node for node in editor.svg_root.iter() if node.tag.endswith('text'))
    assert 'font-size:28' in text.get('style')
    assert 'font-family:Arial' in text.get('style')
    for xml in [b'<!DOCTYPE svg [<!ENTITY x "expanded">]><svg>&x;</svg>',
                b'<svg xmlns="http://www.w3.org/2000/svg"><image href="file:///secret.png"/></svg>']:
        path = tmp_path / 'unsafe.svg'
        path.write_bytes(xml)
        with pytest.raises(ValueError):
            ImageEditor.from_path(path)


def test_raster_layers_move_resize_export_and_history(app, tmp_path):
    from notepadview.image_editor import ImageEditor
    editor = ImageEditor.from_path(png_file(tmp_path))
    editor.add_text('Hello', x=10, y=12)
    editor.set_style(fill='#ff0000', font_family='Arial', font_size=24)
    editor.move_text(35, 25)
    assert editor.layers[0]['x'] == 35
    editor.resize_image(400, 200)
    assert editor.layers[0]['x'] == 70
    destination = tmp_path / 'flattened.jpg'
    editor.save(destination)
    exported = QImage(str(destination))
    assert (exported.width(), exported.height()) == (400, 200)
    assert any(exported.pixelColor(x, y).red() > exported.pixelColor(x, y).green() + 40
               for x in range(60, 210, 3) for y in range(40, 140, 3))
    editor.undo_stack.undo()
    assert editor.layers[0]['x'] == 35
    editor.undo_stack.undo()
    assert editor.layers[0]['x'] == 10
    editor.undo_stack.redo()
    assert editor.layers[0]['x'] == 35
    with pytest.raises(ValueError):
        editor.save(tmp_path / 'bad.svg')


def test_image_session_switch_save_restore_and_failure(window, tmp_path, monkeypatch):
    path = png_file(tmp_path)
    assert window.open_document_path(path)
    session = window.current_session
    editor = session.image_editor
    assert editor is not None
    assert window.save_action.isEnabled() and window.save_as_action.isEnabled()
    editor.add_text('Cached text')
    editor.flip_image(True)
    editor.flip_image(False)
    assert session.document.isModified()
    window.new_document('python')
    window._bind_document(session)
    assert session.image_editor is editor and editor.layers[0]['text'] == 'Cached text'
    assert window.undo_action.isEnabled()
    for _ in range(3):
        window.undo_current()
    assert not session.document.isModified()
    for _ in range(3):
        window.redo_current()
    assert session.document.isModified()
    assert window.flush_workspace()
    path.unlink()
    restored = type(window)(settings=window.settings)
    try:
        restored_session = restored.current_session
        assert restored_session.image_editor.layers[0]['text'] == 'Cached text'
        assert restored_session.image_editor.layers[0]['flip_x']
        assert restored_session.image_editor.layers[0]['flip_y']
        assert restored_session.image_editor._state['raster'] == editor._state['raster']
        assert restored_session.document.isModified()
        monkeypatch.setattr(QMessageBox, 'critical', lambda *args: QMessageBox.Ok)
        assert not restored.save_to(tmp_path / 'missing' / 'image.png')
        assert restored_session.document.isModified()
        destination = tmp_path / 'saved.png'
        assert restored.save_to(destination)
        assert not restored_session.document.isModified()
        assert restored_session.image_editor.undo_stack.isClean()
        restored.undo_current()
        assert restored_session.document.isModified()  # 恢复的编辑有可撤销基线
        restored.redo_current()
        assert not restored_session.document.isModified()
    finally:
        for item in restored.sessions:
            item.document.setModified(False)
        restored.close()


def test_raster_saved_text_keeps_selected_color(app, tmp_path):
    from notepadview.image_editor import ImageEditor
    editor = ImageEditor.from_path(png_file(tmp_path))
    editor.add_text('Hello', x=10, y=10)
    editor.set_style(fill='#ff0000', font_size=30)
    destination = tmp_path / 'red.png'
    editor.save(destination)
    image = QImage(str(destination))
    assert any(image.pixelColor(x, y).red() > image.pixelColor(x, y).green() + 100
               for x in range(200) for y in range(100))


def test_svg_stretched_bounds_and_quoted_internal_references(app, tmp_path):
    from notepadview.image_editor import ImageEditor, safe_svg
    safe_svg(b'<svg xmlns="http://www.w3.org/2000/svg"><rect fill="url(&quot;#gradient&quot;)"/></svg>')
    path = tmp_path / 'wide.svg'
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="400" height="100" viewBox="0 0 200 100"><rect x="10" y="10" width="30" height="20"/></svg>', encoding='utf-8')
    editor = ImageEditor.from_path(path)
    assert editor.select_at(QPointF(25, 20))
    assert not editor.select_at(QPointF(120, 20))


def test_unsafe_svg_reopen_preserves_dirty_session(window, tmp_path, monkeypatch):
    path = svg_file(tmp_path)
    assert window.open_document_path(path)
    session = window.current_session
    session.image_editor.resize_image(400, 200)
    original_editor = session.image_editor
    path.write_bytes(b'<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///invalid">]><svg>&x;</svg>')
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: QMessageBox.Ok)
    assert not window.open_document_path(path)
    assert session.image_editor is original_editor
    assert session.document.isModified()


def test_inactive_image_save_as_and_ratio(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    assert window.open_document_path(png_file(tmp_path))
    session = window.current_session
    editor = session.image_editor
    editor.width_box.setValue(600)
    assert editor.height_box.value() == 300
    editor.resize_image(editor.width_box.value(), editor.height_box.value())
    window.new_document('json')
    current = window.current_session
    destination = tmp_path / 'other.jpg'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(destination), 'JPEG (*.jpg *.jpeg)'))
    assert window.save_as(session=session)
    assert window.current_session is current
    assert session.path == destination and not session.document.isModified()
    assert QImage(str(destination)).size().width() == 600


def test_text_drag_and_editor_disposal(app, tmp_path, capsys):
    from PySide6.QtCore import QPoint, QEvent
    from PySide6.QtTest import QTest
    from notepadview.image_editor import ImageEditor
    editor = ImageEditor.from_path(png_file(tmp_path))
    editor.resize(700, 400)
    editor.show()
    editor.add_text('Drag me', x=20, y=20)
    app.processEvents()
    start = editor.view.mapFromScene(QPointF(30, 30))
    end = start + QPoint(40, 20)
    QTest.mousePress(editor.view.viewport(), Qt.LeftButton, pos=start)
    QTest.mouseMove(editor.view.viewport(), end)
    QTest.mouseRelease(editor.view.viewport(), Qt.LeftButton, pos=end)
    app.processEvents()
    assert editor.layers[0]['x'] > 20
    editor.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
    assert 'Traceback' not in capsys.readouterr().err


def test_svg_save_as_preserves_type_and_cancel_close(window, tmp_path, monkeypatch):
    path = svg_file(tmp_path)
    assert window.open_document_path(path)
    session = window.current_session
    session.image_editor.resize_image(300, 150)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: QMessageBox.Cancel)
    assert not window.close_document_tab(window._session_tab_index(session))
    assert session in window.sessions and session.document.isModified()
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: QMessageBox.Ok)
    assert not window.save_to(tmp_path / 'converted.png')
    assert session.path == path and session.document.isModified()
    assert window.save_to(tmp_path / 'copy.svg')
    assert session.path.name == 'copy.svg'
    assert not session.document.isModified()
