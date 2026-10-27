import importlib
import importlib.util

import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QFontDatabase, QIcon, QRawFont
from PySide6.QtWidgets import QApplication

from notepadview.palettes import theme_colors


SYMBOLS = ('settings', 'theme', 'split', 'files', 'outline', 'add', 'delete', 'select')
SIZES = (16, 20, 24, 32, 48)


@pytest.fixture(scope='module')
def app():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def icons(app):
    assert importlib.util.find_spec('notepadview.control_icons') is not None, 'Control icons are missing'
    return importlib.import_module('notepadview.control_icons')


def visible_pixels(image):
    return [(x, y, image.pixelColor(x, y))
            for y in range(image.height()) for x in range(image.width())
            if image.pixelColor(x, y).alpha() > 100]


@pytest.mark.parametrize('backend', ['system', 'missing_font', 'missing_glyph'])
def test_icons_are_distinct_centered_transparent_and_scale(icons, monkeypatch, backend):
    if backend == 'missing_font':
        monkeypatch.setattr(QFontDatabase, 'families', lambda: [])
    elif backend == 'missing_glyph':
        monkeypatch.setattr(QRawFont, 'supportsCharacter', lambda *args: False)
    images = []
    for symbol in SYMBOLS:
        icon = icons.control_icon(symbol)
        assert isinstance(icon, QIcon) and not icon.isNull()
        assert set(SIZES) <= {size.width() for size in icon.availableSizes()}
        for size in SIZES:
            image = icon.pixmap(size, size).toImage()
            assert image.size() == QSize(size, size)
            ink = visible_pixels(image)
            assert ink
            left, right = min(x for x, _, _ in ink), max(x for x, _, _ in ink)
            top, bottom = min(y for _, y, _ in ink), max(y for _, y, _ in ink)
            assert 0 < left < right < size - 1
            assert 0 < top < bottom < size - 1
            assert abs(left + right - (size - 1)) <= 2
            assert abs(top + bottom - (size - 1)) <= 2
            assert min(right - left, bottom - top) >= size * 0.4
            assert len(ink) < size * size * 0.6
            assert all(image.pixelColor(x, y).alpha() == 0
                       for edge in range(size)
                       for x, y in ((edge, 0), (edge, size - 1), (0, edge), (size - 1, edge)))
        images.append(icon.pixmap(24, 24).toImage())
    assert all(left != right for index, left in enumerate(images) for right in images[index + 1:])


@pytest.mark.parametrize('theme', ['light', 'dark', 'soft'])
def test_all_icon_states_use_theme_colors_at_each_size(icons, theme):
    colors = theme_colors(theme)
    for symbol in SYMBOLS:
        icon = icons.control_icon(symbol, theme)
        for mode, state, role in (
            (QIcon.Normal, QIcon.Off, 'fg'),
            (QIcon.Active, QIcon.Off, 'fg'),
            (QIcon.Disabled, QIcon.Off, 'muted'),
            (QIcon.Disabled, QIcon.On, 'muted'),
            (QIcon.Selected, QIcon.Off, 'accent'),
            (QIcon.Selected, QIcon.On, 'accent'),
            (QIcon.Normal, QIcon.On, 'accent'),
            (QIcon.Active, QIcon.On, 'accent'),
        ):
            expected = QColor(colors[role]).getRgb()[:3]
            for size in SIZES:
                image = icon.pixmap(size, size, mode, state).toImage()
                pixels = [color for _, _, color in visible_pixels(image) if color.alpha() > 200]
                assert pixels, (symbol, theme, mode, state, size)
                assert all(abs(actual - target) <= 1 for color in pixels
                           for actual, target in zip(color.getRgb()[:3], expected))


def test_high_dpi_pixmaps_keep_requested_logical_size(icons):
    for symbol in SYMBOLS:
        icon = icons.control_icon(symbol)
        for size in SIZES:
            pixmap = icon.pixmap(QSize(size, size), 2.0)
            assert pixmap.size() == QSize(size * 2, size * 2)
            assert pixmap.devicePixelRatio() == 2.0
            assert visible_pixels(pixmap.toImage())


def test_missing_glyph_has_same_safe_fallback_as_missing_font(icons, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(QFontDatabase, 'families', lambda: [])
        expected = [icons.control_icon(symbol).pixmap(24, 24).toImage() for symbol in SYMBOLS]
    monkeypatch.setattr(QRawFont, 'supportsCharacter', lambda *args: False)
    actual = [icons.control_icon(symbol).pixmap(24, 24).toImage() for symbol in SYMBOLS]
    assert actual == expected


def test_unknown_symbol_is_rejected_and_unknown_theme_uses_light(icons):
    with pytest.raises(ValueError, match='symbol'):
        icons.control_icon('unknown')
    assert (icons.control_icon('settings', 'unknown').pixmap(24, 24).toImage()
            == icons.control_icon('settings', 'light').pixmap(24, 24).toImage())
