import ctypes
import importlib
import importlib.util
import sys
from ctypes import wintypes
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QCloseEvent, QContextMenuEvent, QColor, QFontDatabase, QIcon, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMainWindow, QMenuBar, QStyleFactory, QToolButton


@pytest.fixture(scope='session')
def app():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def chrome_module():
    assert importlib.util.find_spec('notepadview.window_chrome') is not None, 'ChromeWindow has not been implemented'
    return importlib.import_module('notepadview.window_chrome')


@pytest.fixture
def chrome(app, chrome_module):
    widget = chrome_module.ChromeWindow()
    widget.resize(900, 560)
    widget.show()
    app.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    app.processEvents()


def blank_position(bar):
    return QPoint(bar.width() // 2, bar.height() // 2)


def test_chrome_window_module_is_available():
    assert importlib.util.find_spec('notepadview.window_chrome') is not None


def test_constructor_installs_frameless_menu_and_public_controls(app, chrome_module):
    widget = chrome_module.ChromeWindow()
    try:
        assert isinstance(widget, QMainWindow)
        assert widget.windowFlags() & Qt.FramelessWindowHint
        assert isinstance(widget.menuBar(), QMenuBar)
        assert not widget.menuBar().isNativeMenuBar()
        corner = widget.menuBar().cornerWidget(Qt.TopRightCorner)
        assert corner.objectName() == 'windowControls'
        for name, label in (('minimize', '最小化'), ('maximize', '最大化'), ('close', '关闭')):
            button = getattr(widget, f'window_{name}_button')
            assert isinstance(button, QToolButton)
            assert button.parentWidget() is corner
            assert button.objectName() == f'window{name.title()}Button'
            assert button.property('windowControl') is True
            assert button.toolTip() == button.accessibleName() == label
            assert button.text() == ''
            assert button.width() == 42 and button.height() == 32
            assert not button.icon().isNull()
        assert widget.menuBar().minimumHeight() >= 38
    finally:
        widget.deleteLater()


def test_constructor_does_not_call_subclass_action_or_require_theme(app, chrome_module):
    class IntegratedWindow(chrome_module.ChromeWindow):
        def action(self, *args, **kwargs):
            raise AssertionError('MainWindow.action must not run during base construction')

    widget = IntegratedWindow()
    assert not hasattr(widget, 'theme')
    assert widget.menuBar().cornerWidget() is not None
    widget.deleteLater()


def test_menu_click_keeps_menu_functional_without_starting_move(chrome, monkeypatch):
    menu = chrome.menuBar().addMenu('文件')
    menu.addAction('打开')
    chrome.menuBar().repaint()
    moves = []
    monkeypatch.setattr(chrome.windowHandle(), 'startSystemMove', lambda: moves.append(True) or True)
    position = chrome.menuBar().actionGeometry(menu.menuAction()).center()
    QTest.mouseClick(chrome.menuBar(), Qt.LeftButton, pos=position)
    assert menu.isVisible()
    assert not moves
    menu.close()


def test_blank_menu_press_uses_start_system_move(chrome, monkeypatch):
    moves = []
    monkeypatch.setattr(chrome.windowHandle(), 'startSystemMove', lambda: moves.append(True) or True)
    QTest.mouseClick(chrome.menuBar(), Qt.LeftButton, pos=blank_position(chrome.menuBar()))
    assert moves == [True]


def test_control_corner_does_not_start_move(chrome, monkeypatch):
    moves = []
    monkeypatch.setattr(chrome.windowHandle(), 'startSystemMove', lambda: moves.append(True) or True)
    button = chrome.window_maximize_button
    corner = button.parentWidget()
    position = corner.mapTo(chrome.menuBar(), corner.rect().center())
    QTest.mousePress(chrome.menuBar(), Qt.LeftButton, pos=position)
    QTest.mouseRelease(chrome.menuBar(), Qt.LeftButton, pos=position)
    QTest.mouseClick(button, Qt.LeftButton)
    assert not moves
    assert chrome.isMaximized()


def test_blank_double_click_toggles_maximize(chrome, app):
    QTest.mouseDClick(chrome.menuBar(), Qt.LeftButton, pos=blank_position(chrome.menuBar()))
    app.processEvents()
    assert chrome.isMaximized()
    QTest.mouseDClick(chrome.menuBar(), Qt.LeftButton, pos=blank_position(chrome.menuBar()))
    app.processEvents()
    assert not chrome.isMaximized()


def test_double_click_menu_does_not_maximize(chrome):
    menu = chrome.menuBar().addMenu('文件')
    position = chrome.menuBar().actionGeometry(menu.menuAction()).center()
    QTest.mouseDClick(chrome.menuBar(), Qt.LeftButton, pos=position)
    assert not chrome.isMaximized()
    menu.close()


@pytest.mark.parametrize('system_font', [True, False])
def test_maximize_button_tracks_external_state_and_style_changes(chrome, app, monkeypatch, system_font):
    if not system_font:
        monkeypatch.setattr(QFontDatabase, 'families', lambda: [])
        chrome.refresh_window_icons()
    normal_icon = chrome.window_maximize_button.icon().pixmap(16, 16).toImage()
    chrome.showMaximized()
    app.processEvents()
    button = chrome.window_maximize_button
    assert button.toolTip() == button.accessibleName() == '还原'
    maximized_icon = button.icon().pixmap(16, 16).toImage()
    assert maximized_icon != normal_icon
    QApplication.sendEvent(chrome, QEvent(QEvent.StyleChange))
    assert button.toolTip() == '还原'
    QTest.mouseClick(button, Qt.LeftButton)
    app.processEvents()
    assert not chrome.isMaximized()
    assert button.toolTip() == button.accessibleName() == '最大化'
    assert button.icon().pixmap(16, 16).toImage() != maximized_icon


def test_minimize_button_minimizes(chrome, app):
    QTest.mouseClick(chrome.window_minimize_button, Qt.LeftButton)
    app.processEvents()
    assert chrome.isMinimized()


@pytest.mark.parametrize('system_font', [True, False])
@pytest.mark.parametrize('size', [16, 24, 32])
def test_caption_icons_stay_thin_and_unframed_in_fusion(chrome, monkeypatch, system_font, size):
    style = QStyleFactory.create('Fusion')
    style.setParent(chrome)
    chrome.setStyle(style)
    for name in ('minimize', 'maximize', 'close'):
        getattr(chrome, f'window_{name}_button').setStyle(style)
    if not system_font:
        monkeypatch.setattr(QFontDatabase, 'families', lambda: [])
    chrome.refresh_window_icons()
    for name in ('minimize', 'close', 'maximize'):
        image = getattr(chrome, f'window_{name}_button').icon().pixmap(size, size).toImage()
        ink = [(x, y) for x in range(size) for y in range(size) if image.pixelColor(x, y).alpha() > 100]
        assert ink
        left, right = min(x for x, _ in ink), max(x for x, _ in ink)
        top, bottom = min(y for _, y in ink), max(y for _, y in ink)
        middle_x, middle_y = (left + right) // 2, (top + bottom) // 2
        assert 0 < left < right < size - 1 and 0 < top <= bottom < size - 1
        assert abs(left + right - (size - 1)) <= 2
        assert abs(top + bottom - (size - 1)) <= 2
        if name == 'close':
            # 裸叉的四边中点应透明，避免 Fusion 的带框关闭图标回归。
            assert all(image.pixelColor(x, y).alpha() == 0 for x, y in (
                (middle_x, top), (middle_x, bottom), (left, middle_y), (right, middle_y)))
        elif name == 'maximize':
            assert image.pixelColor(middle_x, middle_y).alpha() == 0
            assert len(ink) < (right - left + 1) * (bottom - top + 1) / 2


@pytest.mark.parametrize('foreground', ['#263142', '#e8eef8', '#34443c'])
def test_refresh_icons_uses_current_palette_and_white_close_active(chrome, foreground):
    palette = chrome.palette()
    palette.setColor(QPalette.Active, QPalette.ButtonText, QColor(foreground))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor('#7e8995'))
    chrome.setPalette(palette)
    chrome.refresh_window_icons()
    for name in ('minimize', 'maximize', 'close'):
        for mode in (QIcon.Normal, QIcon.Disabled, QIcon.Active, QIcon.Selected):
            image = getattr(chrome, f'window_{name}_button').icon().pixmap(16, 16, mode).toImage()
            colors = [image.pixelColor(x, y) for x in range(16) for y in range(16)
                      if image.pixelColor(x, y).alpha() > 200]
            expected = '#7e8995' if mode == QIcon.Disabled else foreground
            if name == 'close' and mode in (QIcon.Active, QIcon.Selected):
                expected = '#ffffff'
            # 预乘 Alpha 的抗锯齿边缘允许一个色阶的舍入误差。
            assert colors and all(abs(actual - target) <= 1 for color in colors
                                  for actual, target in zip(color.getRgb()[:3], QColor(expected).getRgb()[:3]))


@pytest.mark.parametrize('theme', ['light', 'dark', 'soft'])
def test_close_button_hover_renders_white_cross_on_red(chrome, theme):
    from notepadview.palettes import theme_colors
    from notepadview.theme import theme_stylesheet

    style = QStyleFactory.create('Fusion')
    style.setParent(chrome)
    chrome.setStyle(style)
    chrome.setStyleSheet(theme_stylesheet(theme))
    palette = chrome.palette()
    palette.setColor(QPalette.ButtonText, QColor(theme_colors(theme)['fg']))
    chrome.setPalette(palette)
    chrome.refresh_window_icons()
    button = chrome.window_close_button
    # 固定 Qt 悬停状态，避免真实桌面光标影响回归截图。
    button.setAttribute(Qt.WA_UnderMouse, True)
    image = button.grab().toImage()
    assert image.pixelColor(4, button.height() // 2) == QColor('#c42b1c')
    center = image.pixelColor(button.width() // 2, button.height() // 2)
    assert min(center.red(), center.green(), center.blue()) >= 235


def test_close_button_respects_subclass_close_event(app, chrome_module):
    class GuardedWindow(chrome_module.ChromeWindow):
        closed = 0

        def closeEvent(self, event):
            self.closed += 1
            event.ignore()

    widget = GuardedWindow()
    widget.show()
    QTest.mouseClick(widget.window_close_button, Qt.LeftButton)
    assert widget.closed == 1
    assert widget.isVisible()
    widget.hide()
    widget.deleteLater()


def test_chinese_system_menu_is_exposed_and_respects_close_event(chrome, monkeypatch):
    menu = chrome.create_system_menu()
    assert [action.text() for action in menu.actions() if not action.isSeparator()] == [
        '还原', '移动', '大小', '最小化', '最大化', '关闭',
    ]
    assert not menu.actions()[0].isEnabled()
    close_calls = []
    monkeypatch.setattr(chrome, 'close', lambda: close_calls.append(True))
    menu.actions()[-1].trigger()
    assert close_calls == [True]
    menu.deleteLater()


def test_blank_context_menu_uses_chinese_system_menu(chrome, monkeypatch):
    positions = []
    monkeypatch.setattr(chrome, 'show_system_menu', lambda point=None: positions.append(point))
    bar = chrome.menuBar()
    point = blank_position(bar)
    event = QContextMenuEvent(QContextMenuEvent.Mouse, point, bar.mapToGlobal(point))
    QApplication.sendEvent(bar, event)
    assert positions == [bar.mapToGlobal(point)]


@pytest.mark.parametrize('position,expected', [
    ((0, 0), 13), ((400, 0), 12), ((799, 0), 14),
    ((0, 250), 10), ((400, 250), 1), ((799, 250), 11),
    ((0, 499), 16), ((400, 499), 15), ((799, 499), 17),
    ((7, 250), 10), ((8, 250), 1), ((791, 250), 1), ((792, 250), 11),
    ((-1, 0), 0), ((800, 499), 0),
])
def test_eight_pixel_resize_edges(chrome_module, position, expected):
    assert chrome_module._resize_hit_test(*position, 800, 500, 8) == expected


def test_signed_native_coordinates(chrome_module):
    packed = ((-300 & 0xffff) << 16) | (-1400 & 0xffff)
    assert chrome_module._signed_screen_point(packed) == (-1400, -300)


def test_native_hit_test_scales_border_and_uses_physical_negative_rect(chrome, chrome_module, monkeypatch):
    api = SimpleNamespace(window_rect=lambda hwnd: (-1600, -400, 0, 600), dpi=lambda hwnd: 192,
                          monitor_rects=lambda hwnd: None)
    monkeypatch.setattr(chrome_module, '_win32', api)
    message = wintypes.MSG()
    message.hWnd = 1
    message.message = 0x84
    message.lParam = ((-100 & 0xffff) << 16) | (-1585 & 0xffff)
    handled, result = chrome.nativeEvent(b'windows_generic_MSG', ctypes.addressof(message))
    assert handled and result == 10
    message.lParam = ((-100 & 0xffff) << 16) | (-1584 & 0xffff)
    assert chrome.nativeEvent(b'windows_generic_MSG', ctypes.addressof(message)) == (False, 0)
    chrome.showMaximized()
    assert chrome.nativeEvent(b'windows_generic_MSG', ctypes.addressof(message)) == (False, 0)


def test_fixed_size_window_has_no_resize_hit(chrome, chrome_module, monkeypatch):
    chrome.setFixedSize(900, 560)
    monkeypatch.setattr(chrome_module, '_win32', SimpleNamespace(
        window_rect=lambda hwnd: (0, 0, 900, 560), dpi=lambda hwnd: 96))
    message = wintypes.MSG()
    message.message = 0x84
    message.lParam = (250 << 16) | 1
    assert chrome.nativeEvent(b'windows_generic_MSG', ctypes.addressof(message)) == (False, 0)


def test_native_maximize_uses_monitor_work_area_and_minimum_size(chrome, chrome_module, monkeypatch):
    chrome.setMinimumSize(400, 250)
    monkeypatch.setattr(chrome_module, '_win32', SimpleNamespace(
        monitor_rects=lambda hwnd: ((-1920, -200, 0, 880), (-1880, -200, 0, 840)),
        dpi=lambda hwnd: 144))
    info = chrome_module._MINMAXINFO()
    info.ptMaxTrackSize.x, info.ptMaxTrackSize.y = 10000, 10000
    message = wintypes.MSG()
    message.message = 0x24
    message.lParam = ctypes.addressof(info)
    assert chrome.nativeEvent(b'windows_generic_MSG', ctypes.addressof(message)) == (True, 0)
    assert (info.ptMaxPosition.x, info.ptMaxPosition.y) == (40, 0)
    assert (info.ptMaxSize.x, info.ptMaxSize.y) == (1880, 1040)
    assert (info.ptMinTrackSize.x, info.ptMinTrackSize.y) == (600, 375)


def test_native_system_close_keeps_qt_close_event(chrome, chrome_module, monkeypatch):
    monkeypatch.setattr(chrome_module, '_win32', SimpleNamespace())
    message = wintypes.MSG()
    message.message = 0x112
    message.wParam = 0xf060
    assert chrome.nativeEvent(b'windows_generic_MSG', ctypes.addressof(message)) == (False, 0)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows native ABI')
def test_windows_native_structures_match_x64_abi(chrome_module):
    assert ctypes.sizeof(chrome_module._MINMAXINFO) == 40
    assert ctypes.sizeof(chrome_module._MONITORINFO) == 40


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows native window manager')
def test_real_windows_styles_work_area_and_alt_f4(app, chrome_module):
    if app.platformName() != 'windows':
        pytest.skip('Run with QT_QPA_PLATFORM=windows for native integration')

    class GuardedWindow(chrome_module.ChromeWindow):
        closed = 0

        def closeEvent(self, event: QCloseEvent):
            self.closed += 1
            event.ignore()

    widget = GuardedWindow()
    widget.resize(900, 560)
    widget.show()
    app.processEvents()
    try:
        hwnd = int(widget.winId())
        api = chrome_module._win32
        style = api.user32.GetWindowLongPtrW(hwnd, -16)
        assert style & 0x00040000
        assert style & 0x00080000
        widget.showMaximized()
        QTest.qWait(100)
        assert api.window_rect(hwnd) == api.monitor_rects(hwnd)[1]
        api.user32.SendMessageW(hwnd, 0x112, 0xf060, 0)
        app.processEvents()
        assert widget.closed == 1 and widget.isVisible()
    finally:
        widget.hide()
        widget.deleteLater()
