from collections import deque
from pathlib import Path
import struct

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage, QPainter


ICON_SIZES = (16, 20, 24, 32, 48, 64, 128, 256)


def find_brand_source(directory):
    directory = Path(directory)
    for name in ('nvlogo.png', 'NotepadView.jpeg'):
        candidate = directory / name
        if candidate.is_file():
            return candidate
    images = sorted(path for path in directory.iterdir()
                    if path.is_file() and path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp'))
    if len(images) != 1:
        raise ValueError('请在 logo 文件夹放置唯一的标志图片，或将选用图片命名为 nvlogo.png。')
    return images[0]


def remove_outer_background(source):
    if source.isNull():
        raise ValueError('Brand source image could not be loaded')
    image = source.convertToFormat(QImage.Format_RGBA8888)
    width, height = image.width(), image.height()
    pixels = bytearray(image.constBits())
    background = bytearray(
        alpha == 0 or min(red, green, blue) >= 240
        for red, green, blue, alpha in zip(
            pixels[0::4], pixels[1::4], pixels[2::4], pixels[3::4]))
    pending = deque()

    def visit(index):
        if background[index] == 1:
            background[index] = 2
            pending.append(index)

    for x in range(width):
        visit(x)
        visit((height - 1) * width + x)
    for y in range(height):
        visit(y * width)
        visit(y * width + width - 1)

    # Only edge-connected white belongs to the matte, not white ink inside.
    while pending:
        index = pending.popleft()
        x, y = index % width, index // width
        if x:
            visit(index - 1)
        if x + 1 < width:
            visit(index + 1)
        if y:
            visit(index - width)
        if y + 1 < height:
            visit(index + width)

    left, top, right, bottom = width, height, -1, -1
    for index, state in enumerate(background):
        if state == 2:
            pixels[index * 4:index * 4 + 4] = b'\0\0\0\0'
        elif pixels[index * 4 + 3]:
            x, y = index % width, index // width
            left, top = min(left, x), min(top, y)
            right, bottom = max(right, x), max(bottom, y)
    if right < left:
        raise ValueError('Brand source image contains no foreground')
    return QImage(bytes(pixels), width, height, width * 4, QImage.Format_RGBA8888).copy(
        left, top, right - left + 1, bottom - top + 1)


def generate_brand_assets(source, destination):
    foreground = remove_outer_background(QImage(str(source)))
    icon = QImage(512, 512, QImage.Format_ARGB32_Premultiplied)
    # 固定导出 DPI，避免有无桌面应用时生成不同的 PNG 元数据。
    icon.setDotsPerMeterX(3780)
    icon.setDotsPerMeterY(3780)
    icon.fill(Qt.transparent)
    scaled = foreground.scaled(488, 488, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    painter = QPainter(icon)
    painter.drawImage((512 - scaled.width()) // 2, (512 - scaled.height()) // 2, scaled)
    painter.end()

    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    if not icon.save(str(destination / 'app.png'), 'PNG'):
        raise OSError('Could not write brand PNG')

    frames = []
    for size in ICON_SIZES:
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.WriteOnly)
        frame = icon.scaled(size, size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        if not frame.save(buffer, 'PNG'):
            raise OSError('Could not encode brand icon frame')
        buffer.close()
        frames.append(bytes(data))

    offset = 6 + 16 * len(frames)
    directory = bytearray(struct.pack('<HHH', 0, 1, len(frames)))
    for size, frame in zip(ICON_SIZES, frames):
        directory.extend(struct.pack('<BBBBHHII', size % 256, size % 256,
                                     0, 0, 1, 32, len(frame), offset))
        offset += len(frame)
    (destination / 'app.ico').write_bytes(bytes(directory) + b''.join(frames))
