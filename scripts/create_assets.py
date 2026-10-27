from pathlib import Path
import shutil
import sys

from PySide6.QtCore import QLibraryInfo, Qt, QRectF
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QApplication, QStyle

if __package__:
    from .brand_assets import find_brand_source, generate_brand_assets
else:
    from brand_assets import find_brand_source, generate_brand_assets

app = QApplication.instance() or QApplication(sys.argv)
assets = Path(__file__).resolve().parents[1] / 'notepadview' / 'assets'
assets.mkdir(parents=True, exist_ok=True)
generate_brand_assets(find_brand_source(Path(__file__).resolve().parents[1] / 'logo'), assets)
shutil.copy2(Path(QLibraryInfo.path(QLibraryInfo.TranslationsPath)) / 'qtbase_zh_CN.qm',
             assets / 'qtbase_zh_CN.qm')
for name, color in [('light', '#607566'), ('dark', '#a8adb7')]:
    for prefix, standard in [('dropdown', QStyle.SP_ArrowDown), ('tab-close', QStyle.SP_DialogCloseButton)]:
        image = app.style().standardIcon(standard).pixmap(16, 16).toImage()
        painter = QPainter(image)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(image.rect(), QColor(color))
        painter.end()
        image.save(str(assets / (prefix + '-' + name + '.png')))
image = QImage(960, 320, QImage.Format_RGB32)
image.fill(QColor('#edf3f1'))
p = QPainter(image)
p.setRenderHint(QPainter.Antialiasing)
p.setPen(Qt.NoPen)
p.setBrush(QColor('#ffffff'))
p.drawRoundedRect(QRectF(64, 40, 560, 240), 8, 8)
p.setPen(QColor('#222c29'))
p.setFont(QFont('Segoe UI', 24, QFont.Bold))
p.drawText(100, 100, 'Make room for your ideas.')
p.setPen(QPen(QColor('#ccd7d1'), 5, Qt.SolidLine, Qt.RoundCap))
for y, width in [(142, 420), (170, 470), (198, 330), (226, 390)]:
    p.drawLine(102, y, width, y)
p.setPen(Qt.NoPen)
p.setBrush(QColor('#087f68'))
p.drawRoundedRect(QRectF(690, 82, 170, 156), 8, 8)
p.setPen(QColor('white'))
p.setFont(QFont('Consolas', 33, QFont.Bold))
p.drawText(QRectF(690, 82, 170, 156), Qt.AlignCenter, 'NV')
p.end()
image.save(str(assets / 'desk.png'))
