"""无需子类安装的单行菜单栏和 Windows 无边框窗口。"""

import ctypes
import math
import sys
from ctypes import wintypes

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QAction, QFont, QFontDatabase, QFontMetricsF, QIcon, QKeySequence, QPainter,
    QPalette, QPen, QPixmap, QPolygonF, QShortcut,
)
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QMenu, QMenuBar, QToolButton, QWidget


_CAPTION_GLYPHS = {'minimize': '\ue921', 'maximize': '\ue922', 'restore': '\ue923', 'close': '\ue8bb'}


def _caption_font():
    families = QFontDatabase.families()
    for family in ('Segoe Fluent Icons', 'Segoe MDL2 Assets'):
        if family in families:
            font = QFont(family)
            font.setHintingPreference(QFont.PreferFullHinting)
            metrics = QFontMetricsF(font)
            if all(metrics.inFontUcs4(ord(glyph)) for glyph in _CAPTION_GLYPHS.values()):
                return font
    return None


def _caption_pixmap(symbol, size, color, font):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(color)
    if font is not None:
        font = QFont(font)
        font.setPixelSize(round(10 * size / 16))
        painter.setFont(font)
        glyph = _CAPTION_GLYPHS[symbol]
        # 共用方形字形框，避免最小化横线的字体基线留白导致偏移。
        bounds = QFontMetricsF(font).tightBoundingRect(_CAPTION_GLYPHS['maximize'])
        painter.drawText(QPointF(size / 2, size / 2) - bounds.center(), glyph)
    else:
        # 无系统图标字体时保留细线轮廓，避免回到带框的 QStyle 图标。
        painter.scale(size / 16, size / 16)
        painter.setPen(QPen(color, 1, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
        if symbol == 'minimize':
            painter.drawLine(QPointF(3.5, 8.5), QPointF(12.5, 8.5))
        elif symbol == 'maximize':
            painter.drawRect(QRectF(3.5, 3.5, 9, 9))
        elif symbol == 'restore':
            painter.drawPolyline(QPolygonF([QPointF(5.5, 4.5), QPointF(5.5, 3.5),
                                           QPointF(12.5, 3.5), QPointF(12.5, 10.5), QPointF(11.5, 10.5)]))
            painter.drawRect(QRectF(3.5, 5.5, 7, 7))
        else:
            painter.drawLine(QPointF(3.5, 3.5), QPointF(12.5, 12.5))
            painter.drawLine(QPointF(12.5, 3.5), QPointF(3.5, 12.5))
    painter.end()
    return pixmap


class _MINMAXINFO(ctypes.Structure):
    _fields_ = [(name, wintypes.POINT) for name in (
        'ptReserved', 'ptMaxSize', 'ptMaxPosition', 'ptMinTrackSize', 'ptMaxTrackSize')]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT),
               ('rcWork', wintypes.RECT), ('dwFlags', wintypes.DWORD)]


def _signed_screen_point(lparam):
    return ctypes.c_short(lparam & 0xffff).value, ctypes.c_short((lparam >> 16) & 0xffff).value


def _resize_hit_test(x, y, width, height, border, horizontal=True, vertical=True):
    if not (0 <= x < width and 0 <= y < height):
        return 0
    left, right = horizontal and x < border, horizontal and x >= width - border
    top, bottom = vertical and y < border, vertical and y >= height - border
    if top:
        return 13 if left else 14 if right else 12
    if bottom:
        return 16 if left else 17 if right else 15
    return 10 if left else 11 if right else 1


class _Win32:
    def __init__(self):
        self.user32 = ctypes.WinDLL('user32', use_last_error=True)
        declarations = {
            'GetWindowRect': ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            'MonitorFromWindow': ([wintypes.HWND, wintypes.DWORD], wintypes.HANDLE),
            'GetMonitorInfoW': ([wintypes.HANDLE, ctypes.POINTER(_MONITORINFO)], wintypes.BOOL),
            'GetDpiForWindow': ([wintypes.HWND], wintypes.UINT),
            'GetWindowLongPtrW': ([wintypes.HWND, ctypes.c_int], ctypes.c_ssize_t),
            'SetWindowLongPtrW': ([wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t], ctypes.c_ssize_t),
            'SetWindowPos': ([wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT], wintypes.BOOL),
            'SendMessageW': ([wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], ctypes.c_ssize_t),
            'ReleaseCapture': ([], wintypes.BOOL),
        }
        for name, (arguments, result) in declarations.items():
            function = getattr(self.user32, name)
            function.argtypes, function.restype = arguments, result

    def dpi(self, hwnd):
        return self.user32.GetDpiForWindow(hwnd) or 96

    def window_rect(self, hwnd):
        rect = wintypes.RECT()
        if not self.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return rect.left, rect.top, rect.right, rect.bottom

    def monitor_rects(self, hwnd):
        monitor = self.user32.MonitorFromWindow(hwnd, 2)
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        if not monitor or not self.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        return tuple((rect.left, rect.top, rect.right, rect.bottom) for rect in (info.rcMonitor, info.rcWork))

    def install_frame(self, hwnd):
        style = self.user32.GetWindowLongPtrW(hwnd, -16)
        # 保留原生缩放、任务栏和系统快捷键，但由 WM_NCCALCSIZE 去掉标题栏。
        required = 0x00040000 | 0x00080000 | 0x00020000 | 0x00010000
        if style & required != required:
            self.user32.SetWindowLongPtrW(hwnd, -16, style | required)
            self.user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0037)


_win32 = _Win32() if sys.platform == 'win32' else None


class ChromeMenuBar(QMenuBar):
    def __init__(self, owner):
        super().__init__(owner)
        self._chrome_owner = owner
        self.setNativeMenuBar(False)
        self.setMinimumHeight(38)

    def _is_blank(self, point):
        return self.rect().contains(point) and self.actionAt(point) is None and self.childAt(point) is None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._is_blank(event.position().toPoint()):
            handle = self._chrome_owner.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and self._is_blank(event.position().toPoint()):
            self._chrome_owner._toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        if self._is_blank(event.pos()):
            self._chrome_owner.show_system_menu(event.globalPos())
            event.accept()
            return
        super().contextMenuEvent(event)


class ChromeWindow(QMainWindow):
    """子类只需继承；menuBar() 和三个 window_*_button 可直接使用。"""

    def __init__(self, parent=None, flags=Qt.WindowFlags()):
        super().__init__(parent, flags | Qt.FramelessWindowHint)
        self._chrome_ready = False
        bar = ChromeMenuBar(self)
        self.setMenuBar(bar)
        controls = QWidget(bar)
        controls.setObjectName('windowControls')
        layout = QHBoxLayout(controls)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        for name, label, callback in (
            ('minimize', '最小化', self.showMinimized),
            ('maximize', '最大化', self._toggle_maximized),
            ('close', '关闭', lambda: self.close()),
        ):
            button = QToolButton(controls)
            button.setObjectName(f'window{name.title()}Button')
            button.setProperty('windowControl', True)
            button.setFixedSize(42, 32)
            button.setAutoRaise(True)
            button.setIconSize(QSize(16, 16))
            button.setToolButtonStyle(Qt.ToolButtonIconOnly)
            button.setToolTip(label)
            button.setAccessibleName(label)
            button.clicked.connect(callback)
            layout.addWidget(button)
            setattr(self, f'window_{name}_button', button)
        bar.setCornerWidget(controls, Qt.TopRightCorner)
        self._system_menu_shortcut = QShortcut(QKeySequence('Alt+Space'), self)
        self._system_menu_shortcut.activated.connect(self.show_system_menu)
        self._chrome_ready = True
        self.refresh_window_icons()

    def _toggle_maximized(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def refresh_window_icons(self):
        """主题更新 palette 后调用；使用系统细线符号和 ButtonText 颜色。"""
        if not getattr(self, '_chrome_ready', False):
            return
        maximized = self.isMaximized()
        self.window_maximize_button.setToolTip('还原' if maximized else '最大化')
        self.window_maximize_button.setAccessibleName(self.window_maximize_button.toolTip())
        font = _caption_font()
        for button, symbol in (
            (self.window_minimize_button, 'minimize'),
            (self.window_maximize_button, 'restore' if maximized else 'maximize'),
            (self.window_close_button, 'close'),
        ):
            icon = QIcon()
            for mode, group in ((QIcon.Normal, QPalette.Active), (QIcon.Disabled, QPalette.Disabled),
                                (QIcon.Active, QPalette.Active), (QIcon.Selected, QPalette.Active)):
                color = button.palette().color(group, QPalette.ButtonText)
                if button is self.window_close_button and mode in (QIcon.Active, QIcon.Selected):
                    color = Qt.white
                for size in (16, 24, 32, 48, 64):
                    icon.addPixmap(_caption_pixmap(symbol, size, color, font), mode)
            button.setIcon(icon)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.WindowStateChange, QEvent.StyleChange, QEvent.PaletteChange):
            self.refresh_window_icons()

    def showEvent(self, event):
        super().showEvent(event)
        if _win32 is not None and sys.platform == 'win32':
            from PySide6.QtWidgets import QApplication
            if QApplication.platformName() == 'windows':
                _win32.install_frame(int(self.winId()))

    def create_system_menu(self):
        menu = QMenu(self)
        normal = not (self.isMaximized() or self.isFullScreen())
        resizable = self.minimumWidth() < self.maximumWidth() or self.minimumHeight() < self.maximumHeight()
        for text, callback, enabled in (
            ('还原', self.showNormal, not normal or self.isMinimized()),
            ('移动', lambda: self._system_command(0xf010), normal and not self.isMinimized()),
            ('大小', lambda: self._system_command(0xf000), normal and resizable and not self.isMinimized()),
            ('最小化', self.showMinimized, not self.isMinimized()),
            ('最大化', self.showMaximized, normal and resizable),
        ):
            action = QAction(text, menu)
            action.setEnabled(enabled)
            action.triggered.connect(callback)
            menu.addAction(action)
        menu.addSeparator()
        close_action = QAction('关闭', menu)
        close_action.setShortcut(QKeySequence('Alt+F4'))
        close_action.triggered.connect(lambda: self.close())
        menu.addAction(close_action)
        return menu

    def show_system_menu(self, point=None):
        if point is None:
            point = self.menuBar().mapToGlobal(QPoint(0, self.menuBar().height()))
        menu = self.create_system_menu()
        menu.aboutToHide.connect(menu.deleteLater)
        menu.popup(point)

    def _system_command(self, command):
        if _win32 is not None and sys.platform == 'win32':
            _win32.user32.ReleaseCapture()
            _win32.user32.SendMessageW(int(self.winId()), 0x112, command, 0)

    def nativeEvent(self, event_type, message):
        if _win32 is None or not getattr(self, '_chrome_ready', False):
            return False, 0
        if bytes(event_type) not in (b'windows_generic_MSG', b'windows_dispatcher_MSG'):
            return False, 0
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == 0x83:  # WM_NCCALCSIZE：客户区占据整个窗口。
            return True, 0
        if msg.message == 0x84 and not (self.isMaximized() or self.isMinimized() or self.isFullScreen()):
            rect = _win32.window_rect(msg.hWnd)
            if rect is not None:
                left, top, right, bottom = rect
                x, y = _signed_screen_point(msg.lParam)
                scale = _win32.dpi(msg.hWnd) / 96
                hit = _resize_hit_test(x - left, y - top, right - left, bottom - top,
                                       max(1, round(8 * scale)),
                                       self.minimumWidth() < self.maximumWidth(),
                                       self.minimumHeight() < self.maximumHeight())
                if hit not in (0, 1):
                    bar = self.menuBar()
                    point = bar.mapFrom(self, QPoint(round((x - left) / scale), round((y - top) / scale)))
                    if not (bar.rect().contains(point) and not bar._is_blank(point)):
                        return True, hit
        if msg.message == 0x24 and msg.lParam and not self.isFullScreen():  # WM_GETMINMAXINFO
            rects = _win32.monitor_rects(msg.hWnd)
            if rects is not None:
                monitor, work = rects
                info = _MINMAXINFO.from_address(msg.lParam)
                info.ptMaxPosition.x, info.ptMaxPosition.y = work[0] - monitor[0], work[1] - monitor[1]
                info.ptMaxSize.x, info.ptMaxSize.y = work[2] - work[0], work[3] - work[1]
                scale = _win32.dpi(msg.hWnd) / 96
                info.ptMinTrackSize.x = math.ceil(self.minimumWidth() * scale)
                info.ptMinTrackSize.y = math.ceil(self.minimumHeight() * scale)
                if self.maximumWidth() < 16777215:
                    info.ptMaxTrackSize.x = math.floor(self.maximumWidth() * scale)
                if self.maximumHeight() < 16777215:
                    info.ptMaxTrackSize.y = math.floor(self.maximumHeight() * scale)
                return True, 0
        return False, 0
