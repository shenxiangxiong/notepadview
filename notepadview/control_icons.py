"""Themed control icons using Windows symbol fonts or Qt line drawing."""

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor, QFont, QFontDatabase, QIcon, QPainter, QPainterPath, QPen,
    QPixmap, QPolygonF, QRawFont,
)

from .palettes import theme_colors


_GLYPHS = {
    'settings': '\ue713',
    'theme': '\ue790',
    'split': '\ue89f',
    'files': '\ue8b7',
    'outline': '\ue8fd',
    'add': '\ue710',
    'delete': '\ue74d',
    'select': '\ue762',
}
# Extra physical sizes keep the requested logical sizes sharp at 200% DPI.
_SIZES = (16, 20, 24, 32, 48, 64, 96)


def _symbol_font(glyph):
    families = QFontDatabase.families()
    for family in ('Segoe Fluent Icons', 'Segoe MDL2 Assets'):
        if family not in families:
            continue
        font = QFont(family)
        font.setPixelSize(32)
        font.setStyleStrategy(QFont.NoFontMerging)
        font.setHintingPreference(QFont.PreferFullHinting)
        raw = QRawFont.fromFont(font)
        if raw.isValid() and raw.supportsCharacter(ord(glyph)):
            return font
    return None


def _draw_fallback(painter, symbol, color):
    painter.setPen(QPen(color, 1.25, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush)
    if symbol == 'settings':
        points = []
        for step in range(32):
            angle = math.tau * (step + 0.5) / 32
            radius = 6 if step % 4 in (1, 2) else 4.8
            points.append(QPointF(8 + radius * math.cos(angle), 8 + radius * math.sin(angle)))
        painter.drawPolygon(QPolygonF(points))
        painter.drawEllipse(QPointF(8, 8), 2.1, 2.1)
    elif symbol == 'theme':
        painter.drawEllipse(QPointF(8, 8), 2.8, 2.8)
        for step in range(8):
            angle = math.tau * step / 8
            painter.drawLine(QPointF(8 + 4.7 * math.cos(angle), 8 + 4.7 * math.sin(angle)),
                             QPointF(8 + 6 * math.cos(angle), 8 + 6 * math.sin(angle)))
    elif symbol == 'split':
        painter.drawRoundedRect(QRectF(2.5, 3, 11, 10), 0.5, 0.5)
        painter.drawLine(QPointF(8, 3), QPointF(8, 13))
    elif symbol == 'add':
        painter.drawLine(QPointF(8, 3), QPointF(8, 13))
        painter.drawLine(QPointF(3, 8), QPointF(13, 8))
    elif symbol == 'delete':
        painter.drawLine(QPointF(3, 4.5), QPointF(13, 4.5))
        painter.drawRoundedRect(QRectF(6, 2.5, 4, 2), 0.5, 0.5)
        painter.drawPolygon(QPolygonF([
            QPointF(4, 4.5), QPointF(5, 13.5),
            QPointF(11, 13.5), QPointF(12, 4.5),
        ]))
        for x in (6.5, 9.5):
            painter.drawLine(QPointF(x, 7), QPointF(x, 11))
    elif symbol == 'select':
        painter.drawRoundedRect(QRectF(2.5, 2.5, 11, 11), 1, 1)
        painter.drawPolyline(QPolygonF([
            QPointF(5, 8), QPointF(7, 10), QPointF(11, 6),
        ]))
    elif symbol in ('replace', 'replace-all'):
        font = QFont('Segoe UI')
        font.setPixelSize(7)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QRectF(0, -1, 16, 8), Qt.AlignCenter,
                         'ab' if symbol == 'replace-all' else 'b')
        painter.drawLine(QPointF(12, 6), QPointF(4, 6))
        painter.drawPolyline(QPolygonF([QPointF(6, 4), QPointF(4, 6), QPointF(6, 8)]))
        painter.drawRoundedRect(QRectF(2, 9, 12 if symbol == 'replace-all' else 8, 6), 0.5, 0.5)
        painter.drawText(QRectF(2, 8, 12 if symbol == 'replace-all' else 8, 8),
                         Qt.AlignCenter, 'ac' if symbol == 'replace-all' else 'c')
    elif symbol in ('find-up', 'find-down'):
        up = symbol == 'find-up'
        painter.drawLine(QPointF(8, 13 if up else 3), QPointF(8, 3 if up else 13))
        painter.drawPolyline(QPolygonF([
            QPointF(4, 7 if up else 9), QPointF(8, 3 if up else 13),
            QPointF(12, 7 if up else 9),
        ]))
    elif symbol == 'close':
        painter.drawLine(QPointF(4, 4), QPointF(12, 12))
        painter.drawLine(QPointF(12, 4), QPointF(4, 12))
    elif symbol == 'files':
        painter.drawPolygon(QPolygonF([
            QPointF(2.5, 3.5), QPointF(6.5, 3.5), QPointF(8, 5.5),
            QPointF(13.5, 5.5), QPointF(13.5, 12.5), QPointF(2.5, 12.5),
        ]))
        painter.drawLine(QPointF(2.5, 6.5), QPointF(13.5, 6.5))
    else:
        painter.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        for y in (4, 8, 12):
            painter.setBrush(color)
            painter.drawEllipse(QPointF(3.5, y), 0.5, 0.5)
            painter.drawLine(QPointF(6, y), QPointF(13, y))


def _pixmap(symbol, size, color, path):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    try:
        if path is not None:
            bounds = path.boundingRect()
            scale = size * 0.76 / max(bounds.width(), bounds.height())
            painter.translate(size / 2, size / 2)
            painter.scale(scale, scale)
            painter.translate(-bounds.center())
            # A slight physical stroke keeps small font contours legible.
            pen = QPen(color, 0.35)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(color)
            painter.drawPath(path)
        else:
            painter.scale(size / 16, size / 16)
            _draw_fallback(painter, symbol, color)
        # Tint the finished alpha mask once to avoid accumulated RGB rounding.
        painter.resetTransform()
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), color)
    finally:
        painter.end()
    return pixmap


def control_icon(symbol, theme='light') -> QIcon:
    """Create a transparent icon; call again when the application's theme changes.

    Disabled controls use muted even when checked; other checked or selected
    controls use accent. Unknown themes follow theme_colors' light fallback.
    A Qt GUI application must exist before creating icons.
    """
    drawn_symbols = ('replace', 'replace-all', 'find-up', 'find-down', 'close')
    if symbol not in _GLYPHS and symbol not in drawn_symbols:
        raise ValueError(f'Unknown control icon symbol: {symbol!r}')
    font = _symbol_font(_GLYPHS[symbol]) if symbol in _GLYPHS else None
    path = None
    if font is not None:
        candidate = QPainterPath()
        candidate.addText(QPointF(0, 0), font, _GLYPHS[symbol])
        if not candidate.isEmpty() and not candidate.boundingRect().isEmpty():
            path = candidate
    colors = theme_colors(theme)
    icon = QIcon()
    for mode in (QIcon.Normal, QIcon.Active, QIcon.Disabled, QIcon.Selected):
        for state in (QIcon.Off, QIcon.On):
            role = ('muted' if mode == QIcon.Disabled else
                    'accent' if mode == QIcon.Selected or state == QIcon.On else 'fg')
            color = QColor(colors[role])
            for size in _SIZES:
                icon.addPixmap(_pixmap(symbol, size, color, path), mode, state)
    return icon
