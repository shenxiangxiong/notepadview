from pathlib import Path
import struct

from PySide6.QtGui import QColor, QImage


ROOT = Path(__file__).resolve().parents[1]
ICON_SIZES = (16, 20, 24, 32, 48, 64, 128, 256)


def test_brand_source_uses_current_logo_and_accepts_single_renamed_image(tmp_path):
    from scripts.brand_assets import find_brand_source

    current = tmp_path / 'nvlogo.png'
    current.touch()
    (tmp_path / 'NotepadView.jpeg').touch()
    assert find_brand_source(tmp_path) == current
    current.unlink()
    assert find_brand_source(tmp_path).name == 'NotepadView.jpeg'
    (tmp_path / 'NotepadView.jpeg').rename(tmp_path / 'custom.png')
    assert find_brand_source(tmp_path).name == 'custom.png'


def test_background_removal_crops_outer_white_and_preserves_enclosed_white():
    from scripts.brand_assets import remove_outer_background

    source = QImage(40, 40, QImage.Format_RGBA8888)
    source.fill(QColor(250, 250, 250))
    for y in range(8, 32):
        for x in range(12, 28):
            source.setPixelColor(x, y, QColor('#087fff'))
    for x in range(16, 24):
        source.setPixelColor(x, 20, QColor('white'))
    source.setPixelColor(12, 8, QColor(248, 248, 248))

    cropped = remove_outer_background(source)

    assert cropped.size().toTuple() == (16, 24)
    assert cropped.pixelColor(0, 0).alpha() == 0
    assert cropped.pixelColor(4, 12).getRgb() == (255, 255, 255, 255)
    assert cropped.pixelColor(1, 1) == QColor('#087fff')
    assert source.pixelColor(0, 0).alpha() == 255


def test_generated_brand_assets_are_transparent_multisize_and_repeatable(tmp_path):
    from scripts.brand_assets import find_brand_source, generate_brand_assets

    source = find_brand_source(ROOT / 'logo')
    generate_brand_assets(source, tmp_path)
    first = {name: (tmp_path / name).read_bytes() for name in ('app.png', 'app.ico')}
    generate_brand_assets(source, tmp_path)
    assert first == {name: (tmp_path / name).read_bytes() for name in first}

    png = QImage(str(tmp_path / 'app.png'))
    assert png.size().toTuple() == (512, 512)
    assert png.hasAlphaChannel()
    assert (png.dotsPerMeterX(), png.dotsPerMeterY()) == (3780, 3780)
    assert png.pixelColor(0, 0).alpha() == 0
    assert png.pixelColor(256, 256).alpha() == 255
    opaque = [(x, y) for y in range(512) for x in range(512)
              if png.pixelColor(x, y).alpha() > 127]
    assert 475 <= max(y for x, y in opaque) - min(y for x, y in opaque) <= 500
    assert 380 <= max(x for x, y in opaque) - min(x for x, y in opaque) <= 440
    assert any(png.pixelColor(x, y).alpha() == 255
               and min(png.pixelColor(x, y).getRgb()[:3]) > 240 for x, y in opaque)

    data = first['app.ico']
    assert struct.unpack_from('<HHH', data) == (0, 1, len(ICON_SIZES))
    for index, size in enumerate(ICON_SIZES):
        width, height, colors, reserved, planes, depth, length, offset = struct.unpack_from(
            '<BBBBHHII', data, 6 + 16 * index)
        assert (width or 256, height or 256) == (size, size)
        assert (colors, reserved, planes, depth) == (0, 0, 1, 32)
        image = QImage.fromData(data[offset:offset + length], 'PNG')
        assert image.size().toTuple() == (size, size)
        assert image.hasAlphaChannel()
        assert image.pixelColor(0, 0).alpha() == 0
        assert image.pixelColor(size // 2, size // 2).alpha() == 255


def test_packaged_assets_use_the_same_generated_brand(tmp_path):
    from scripts.brand_assets import find_brand_source, generate_brand_assets

    generate_brand_assets(find_brand_source(ROOT / 'logo'), tmp_path)
    for name in ('app.png', 'app.ico'):
        assert (ROOT / 'notepadview' / 'assets' / name).read_bytes() == (tmp_path / name).read_bytes()


def test_create_assets_delegates_brand_generation_without_drawing_old_icon():
    import ast

    tree = ast.parse((ROOT / 'scripts' / 'create_assets.py').read_text(encoding='utf-8'))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert any(isinstance(call.func, ast.Name) and call.func.id == 'generate_brand_assets'
               for call in calls)
    assert not any(isinstance(node, ast.Constant) and node.value == 'app.ico'
                   for node in ast.walk(tree))
