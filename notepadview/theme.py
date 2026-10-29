from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPalette, QPixmap, QRegion
from PySide6.QtWidgets import QApplication, QComboBox, QFontComboBox, QMenu, QMenuBar, QStyle, QWidget

from notepadview.palettes import THEMES, theme_colors
from notepadview.control_icons import control_icon


def _round_popup(popup):
    if popup.size().isEmpty():
        popup.clearMask()
        return
    path = QPainterPath()
    radius = min(8, popup.width() / 2, popup.height() / 2)
    path.addRoundedRect(QRectF(popup.rect()), radius, radius)
    popup.setMask(QRegion(path.toFillPolygon().toPolygon()))


def style_popup(popup, theme):
    colors = theme_colors(theme)
    selector = 'QMenu' if isinstance(popup, QMenu) else 'QComboBoxPrivateContainer'
    padding = 5 if isinstance(popup, QMenu) else 0
    highlight = 'hover' if isinstance(popup, QMenu) else 'selection'
    highlighted_text = 'accent' if isinstance(popup, QMenu) else 'fg'
    palette = QPalette(popup.palette())
    for role, key in ((QPalette.Window, 'bg'), (QPalette.Base, 'bg'),
                      (QPalette.WindowText, 'fg'), (QPalette.Text, 'fg'),
                      (QPalette.Highlight, highlight), (QPalette.HighlightedText, highlighted_text)):
        palette.setColor(role, QColor(colors[key]))
    popup.setPalette(palette)
    popup.setStyleSheet(f'{selector} {{ background:{colors["bg"]}; color:{colors["fg"]}; '
                       f'border:1px solid {colors["border"]}; border-radius:8px; padding:{padding}px; }}')
    _round_popup(popup)


def _position_combo_popup(popup):
    combo = popup.parentWidget()
    if not isinstance(combo, QComboBox):
        return
    top = combo.mapToGlobal(QPoint(0, 0))
    below = top.y() + combo.height() + 2
    available = combo.screen().availableGeometry()
    above = top.y() - popup.height() - 2
    if below + popup.height() <= available.bottom() + 1:
        y = below
    elif above >= available.top():
        y = above
    else:
        y = max(available.top(), min(below, available.bottom() - popup.height() + 1))
    x = max(available.left(), min(top.x(), available.right() - popup.width() + 1))
    popup.move(x, y)


class PopupThemeFilter(QObject):
    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Show, QEvent.Resize) and isinstance(watched, QWidget) and watched.isWindow():
            if not isinstance(watched, QMenu) and watched.metaObject().className() != 'QComboBoxPrivateContainer':
                return False
            owner = watched.parent()
            while owner is not None:
                theme = getattr(owner, 'theme', None)
                if theme in THEMES:
                    if event.type() == QEvent.Show:
                        style_popup(watched, theme)
                        if not isinstance(watched, QMenu):
                            _position_combo_popup(watched)
                            QTimer.singleShot(0, watched, lambda: _position_combo_popup(watched))
                    else:
                        _round_popup(watched)
                        # Windows 风格会在 resize 处理期间清空遮罩，随后重新应用。
                        QTimer.singleShot(0, watched, lambda: _round_popup(watched))
                    break
                owner = owner.parent()
        return False


def _watch_popup(popup):
    app = QApplication.instance()
    if not hasattr(app, '_notepad_popup_filter'):
        app._notepad_popup_filter = PopupThemeFilter(app)
    popup.installEventFilter(app._notepad_popup_filter)


class ThemedMenu(QMenu):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _watch_popup(self)

    def addMenu(self, *args):
        if len(args) == 1 and isinstance(args[0], QMenu):
            _watch_popup(args[0])
            return super().addMenu(*args)
        menu = ThemedMenu(args[-1], self)
        if len(args) == 2:
            menu.setIcon(args[0])
        super().addMenu(menu)
        return menu


class ThemedMenuBar(QMenuBar):
    def addMenu(self, *args):
        if len(args) == 1 and isinstance(args[0], QMenu):
            _watch_popup(args[0])
            return super().addMenu(*args)
        menu = ThemedMenu(args[-1], self)
        if len(args) == 2:
            menu.setIcon(args[0])
        super().addMenu(menu)
        return menu


class ThemedComboBox(QComboBox):
    def showPopup(self):
        _watch_popup(self.view().window())
        super().showPopup()


class ThemedFontComboBox(QFontComboBox):
    def showPopup(self):
        _watch_popup(self.view().window())
        super().showPopup()


def theme_stylesheet(theme):
    colors = theme_colors(theme)
    bg, pane, fg, muted, border, hover, accent = (colors[key] for key in
        ('bg', 'pane', 'fg', 'muted', 'border', 'hover', 'accent'))
    arrow = (Path(__file__).resolve().parent / 'assets' /
             ('dropdown-dark.png' if theme in ('dark', 'nv') else 'dropdown-light.png')).as_posix()
    spin_assets = Path(__file__).resolve().parent / 'assets'
    spin_theme = theme if theme in THEMES else 'light'
    spin_up = (spin_assets / f'spin-up-{spin_theme}.svg').as_posix()
    spin_down = (spin_assets / f'spin-down-{spin_theme}.svg').as_posix()
    return f'''
QAbstractSpinBox {{ padding-right:2px; border:1px solid {border}; border-radius:5px; background:{bg}; selection-background-color:{accent}; }}
QAbstractSpinBox::up-button {{ subcontrol-origin:border; subcontrol-position:top right; width:22px; margin:2px 2px 0 0; border:0; border-top-right-radius:3px; background:transparent; }}
QAbstractSpinBox::down-button {{ subcontrol-origin:border; subcontrol-position:bottom right; width:22px; margin:0 2px 2px 0; border:0; border-bottom-right-radius:3px; background:transparent; }}
QAbstractSpinBox::up-button:hover,QAbstractSpinBox::down-button:hover {{ background:{hover}; }}
QAbstractSpinBox::up-button:pressed,QAbstractSpinBox::down-button:pressed {{ background:{colors['selection']}; }}
QAbstractSpinBox::up-arrow {{ image:url("{spin_up}"); width:12px; height:10px; }}
QAbstractSpinBox::down-arrow {{ image:url("{spin_down}"); width:12px; height:10px; }}
QAbstractSpinBox::up-arrow:disabled,QAbstractSpinBox::down-arrow:disabled {{ width:8px; height:7px; }}
QWidget {{ background:{bg}; color:{fg}; font-family:'Microsoft YaHei UI','Segoe UI'; font-size:13px; }}
QWidget#imageToolsPanel,QWidget#imageStylesPanel {{ background:{pane}; border:1px solid {border}; border-radius:8px; }}
QWidget#imageToolsPanel QLabel,QWidget#imageStylesPanel QLabel,QWidget#imageToolsPanel QCheckBox {{ background:transparent; border:0; }}
QWidget#imageEditor QPushButton {{ min-height:28px; padding:2px 12px; border:1px solid {border}; border-radius:5px; background:{bg}; }}
QWidget#imageEditor QPushButton:hover {{ background:{hover}; border-color:{accent}; }}
QWidget#imageEditor QFontComboBox,QWidget#imageEditor QFontComboBox QAbstractItemView {{ font-size:12px; }}
QWidget#imageEditor QSpinBox,QWidget#imageEditor QDoubleSpinBox,QWidget#imageEditor QFontComboBox {{ min-height:28px; padding:2px 8px; border:1px solid {border}; border-radius:5px; background:{bg}; }}
QWidget#imageEditor QSpinBox,QWidget#imageEditor QDoubleSpinBox {{ min-height:32px; padding:2px 2px 2px 10px; font-size:14px; }}
QWidget#imageEditor QSpinBox:focus,QWidget#imageEditor QDoubleSpinBox:focus,QWidget#imageEditor QFontComboBox:focus {{ border-color:{accent}; }}
QWidget#pdfToolsPanel {{ background:{pane}; border:1px solid {border}; border-radius:8px; }}
QWidget#pdfToolsPanel QLabel {{ background:transparent; border:0; color:{muted}; }}
QFrame#pdfToolDivider {{ background:{border}; border:0; }}
QWidget#pdfNavigation {{ background:{pane}; border:1px solid {border}; border-radius:10px; }}
QWidget#pdfNavigation QLabel {{ background:transparent; border:0; color:{muted}; font-size:12px; padding:0 4px; }}
QWidget#pdfNavigation QPushButton {{ min-height:28px; padding:2px 12px; background:transparent; border:1px solid transparent; border-radius:6px; }}
QWidget#pdfNavigation QPushButton:hover {{ background:{hover}; border-color:{border}; }}
QWidget#pdfNavigation QPushButton:disabled {{ color:{muted}; background:transparent; border-color:transparent; }}
QWidget#pdfNavigation QSpinBox {{ min-height:28px; padding:2px 6px; background:{bg}; color:{accent}; border:1px solid {border}; border-radius:6px; font-size:12px; font-weight:600; }}
QWidget#pdfNavigation QSpinBox:focus {{ border-color:{accent}; }}
QScrollArea#pdfToolbarScroll {{ background:transparent; border:0; }}
QWidget#pdfToolsPanel QPushButton {{ min-height:28px; padding:2px 8px; }}
QWidget#pdfToolsPanel QComboBox,QWidget#pdfToolsPanel QLineEdit {{ min-height:28px; padding:2px 8px; border:1px solid {border}; border-radius:5px; background:{bg}; }}
QWidget#pdfToolsPanel QComboBox:focus,QWidget#pdfToolsPanel QLineEdit:focus {{ border-color:{accent}; }}
QWidget#pdfToolsPanel QFontComboBox,QWidget#pdfToolsPanel QFontComboBox QAbstractItemView {{ font-size:12px; }}
QWidget#pdfToolsPanel QSpinBox,QWidget#pdfToolsPanel QDoubleSpinBox {{ min-height:28px; padding:2px 6px; }}
QLabel#imageEditorHint {{ color:{muted}; font-size:11px; padding:0 2px 3px 2px; background:transparent; }}
QGraphicsView#imageCanvas {{ background:{pane}; border:1px solid {border}; border-radius:8px; }}
QMenuBar {{ background:{colors.get('header', pane)}; border-bottom:1px solid {border}; border-radius:6px; padding:3px 10px; }}
QMenuBar::item {{ padding:5px 9px; border-radius:4px; }}
QMenuBar::item:selected,QMenu::item:selected {{ background:{hover}; }}
QWidget#windowControls {{ background:transparent; }}
QToolButton[windowControl="true"] {{ padding:0; margin:0; min-width:42px; max-width:42px; min-height:32px; max-height:32px; border:0; border-radius:4px; background:transparent; }}
QToolButton[windowControl="true"]:hover {{ background:{hover}; }}
QToolButton#windowCloseButton:hover {{ background:#c42b1c; color:white; }}
QToolButton#windowCloseButton:pressed {{ background:#a61f16; color:white; }}
QToolButton#headerButton,QToolButton#sidebarButton {{ min-width:32px; max-width:32px;
    min-height:32px; max-height:32px; padding:0; margin:0; border:0; border-radius:6px; background:transparent; }}
QToolButton#headerButton:hover,QToolButton#sidebarButton:hover {{ background:{hover}; }}
QToolButton#headerButton:checked,QToolButton#sidebarButton:checked {{ background:{colors['selection']}; color:{accent}; }}
QToolButton#headerButton::menu-indicator {{ image:none; width:0; height:0; }}
QMenu {{ background:{bg}; border:1px solid {border}; border-radius:8px; padding:5px; }}
QMenu::item {{ padding:8px 30px; border-radius:4px; }}
QMenu::item:disabled {{ color:{muted}; }}
QMenu::separator {{ height:1px; background:{border}; margin:4px 8px; }}
QToolBar {{ border:0; border-bottom:1px solid {border}; border-radius:6px; spacing:5px; padding:8px 12px; background:{bg}; }}
QToolBar::separator {{ width:1px; background:{border}; margin:5px 7px; }}
QToolButton {{ padding:6px; min-width:20px; border:1px solid transparent; border-radius:4px; }}
QToolButton#scrollButton {{ padding:0; min-width:0; font-family:'Segoe UI Symbol'; font-size:17px; font-weight:600; }}
QToolButton:hover,QPushButton:hover {{ background:{hover}; border-color:{border}; }}
QToolButton:pressed,QPushButton:pressed {{ background:{colors['selection']}; }}
QToolButton:checked {{ background:{hover}; color:{accent}; border-color:{border}; }}
QToolButton:disabled,QPushButton:disabled {{ color:{muted}; }}
QToolButton:focus,QPushButton:focus {{ border-color:{accent}; }}
QWidget#brandGroup,QLabel#brandIcon {{ background:transparent; }}
QLabel#brand {{ font-size:13px; font-weight:700; color:{colors.get('violet', accent)}; background:transparent; }}
QWidget#sidebar,QWidget#sidebar QLabel {{ background:{pane}; }}
QWidget#sidebar {{ border-radius:0; }}
QWidget#sidebarStack,QWidget#filesPage {{ background:{pane}; }}
QLabel#sidebarTitle {{ font-size:13px; font-weight:600; padding:0 6px; color:{fg}; }}
QWidget#sidebarGroup {{ background:{pane}; }}
QWidget#sidebarRail {{ background:{pane}; border-left:1px solid {border}; border-right:0; }}
QWidget#sidebarRail[position="left"] {{ border-left:0; border-right:1px solid {border}; }}
QTreeWidget#fileTree {{ background:{pane}; alternate-background-color:{bg}; color:{fg};
    border:0; outline:0; padding:0;
    selection-background-color:{colors['selection']}; selection-color:{fg}; show-decoration-selected:1; }}
QTreeWidget#fileTree::item {{ min-height:30px; padding:2px 8px; border:0; border-radius:2px; }}
QTreeWidget#fileTree::item:hover {{ background:{hover}; }}
QTreeWidget#fileTree::item:selected {{ background:{colors['selection']}; color:{fg}; }}
QTreeWidget#fileTree QHeaderView {{ background:{pane}; color:{muted}; border:0; }}
QTreeWidget#fileTree QHeaderView::section {{ background:{pane}; color:{muted}; font-size:11px;
    font-weight:600; padding:7px 8px; border:0; border-bottom:1px solid {border}; border-radius:4px; }}
QTreeWidget#fileTree QHeaderView::section:hover {{ background:{hover}; color:{fg}; }}
QTreeWidget#fileTree QAbstractButton {{ background:{pane}; border:0; border-radius:4px; }}
QLabel#sectionLabel {{ color:{muted}; font-size:11px; padding:0 4px 14px; }}
QWidget#documentTabStrip {{ background:{pane}; border-bottom:1px solid {border}; border-radius:6px; }}
QTabBar#documentTabs {{ background:transparent; border:0; }}
QTabBar#documentTabs::tab {{ background:transparent; color:{muted}; min-width:90px; max-width:196px;
    height:26px; padding:4px 12px; margin-right:5px; border:1px solid transparent;
    border-top:2px solid transparent; border-radius:6px; }}
QTabBar#documentTabs::tab:hover {{ background:{hover}; color:{fg}; }}
QTabBar#documentTabs::tab:selected {{ background:{bg}; color:{fg}; border-color:{border};
    border-top-color:{accent}; border-bottom-color:{bg}; }}
QTabBar#documentTabs QToolButton {{ background:{pane}; border:1px solid {border}; padding:2px; min-width:18px; }}
QToolButton#addDocumentButton {{ font-size:21px; padding:0; min-width:0; margin-bottom:4px; color:{muted}; }}
QListWidget {{ background:transparent; border:0; outline:0; padding-top:10px; }}
QListWidget::item {{ padding:9px 5px; border-radius:4px; }}
QListWidget::item:selected {{ background:{hover}; color:{accent}; }}
QListWidget::item:hover {{ background:{hover}; }}
QWidget#fileListRow,QLabel#fileNameLabel {{ background:transparent; }}
QWidget#workspaceContainer QToolBar,QWidget#workspaceContainer QLabel#paneFooter,
QWidget#workspaceContainer QSplitter::handle {{ border-radius:0; }}
QWidget#previewPanel {{ background:{colors.get('preview_frame', bg)}; border:0; }}
QWidget#previewPanel QLabel {{ background:{colors.get('preview_frame', bg)}; }}
QWidget#scrollControls {{ background:transparent; border:0; }}
QWidget#scrollControls QToolButton#scrollButton {{ background:{pane}; border:1px solid {border}; border-radius:4px; }}
QLabel#paneFooter {{ color:{muted}; font-size:10px; border-top:1px solid {border}; border-radius:4px; padding:4px 8px; }}
QLabel#mutedLabel {{ color:{muted}; padding:12px 4px; }}
QSplitter::handle {{ background:{border}; width:1px; border-radius:1px; }}
QStatusBar {{ background:{pane}; border-top:1px solid {border}; border-radius:6px; padding:3px 12px; }}
QStatusBar QLabel,QStatusBar QToolButton {{ background:transparent; color:{muted}; font-size:11px; }} QStatusBar::item {{ border:0; }}
QStatusBar QToolButton::menu-indicator {{ image:none; width:0; height:0; }}
QLineEdit,QComboBox {{ background:{bg}; border:1px solid {border}; border-radius:5px;
    padding:6px 10px; min-height:20px; selection-background-color:{colors['selection']}; selection-color:{fg}; }}
QLineEdit:hover,QComboBox:hover {{ border-color:{muted}; }}
QLineEdit:focus,QComboBox:focus,QComboBox:on {{ border-color:{accent}; }}
QComboBox {{ min-width:85px; padding:5px 32px 5px 10px; border-radius:4px;
    background:{bg}; color:{fg}; }}
QComboBox:hover {{ background:{hover}; border-color:{border}; }}
QComboBox:focus,QComboBox:on {{ border-color:{accent}; }}
QComboBox:disabled {{ background:{pane}; color:{muted}; border-color:{border}; }}
QComboBox:editable QLineEdit {{ background:transparent; border:0; border-radius:0; padding:0; min-height:0; }}
QComboBox::drop-down {{ subcontrol-origin:padding; subcontrol-position:top right;
    width:28px; border:0; background:transparent; }}
QComboBox::down-arrow {{ image:url("{arrow}"); width:12px; height:12px; }}
QComboBox QAbstractItemView {{ background:{bg}; color:{fg}; border:0; border-radius:7px;
    outline:0; padding:4px; selection-background-color:{colors['selection']}; selection-color:{fg}; }}
QComboBox QAbstractItemView::item {{ min-height:28px; padding:3px 10px; border:0; border-radius:3px; }}
QComboBox QAbstractItemView::item:hover {{ background:{hover}; color:{fg}; }}
QComboBox QAbstractItemView::item:selected {{ background:{colors['selection']}; color:{fg}; }}
QComboBox QAbstractItemView::item:disabled {{ color:{muted}; }}
QComboBoxPrivateContainer {{ background:{bg}; border:1px solid {border}; border-radius:8px; }}
QPushButton {{ background:{bg}; border:1px solid {border}; border-radius:5px; padding:6px 14px; min-height:20px; }}
QPushButton#primaryButton {{ background:{accent}; color:{bg}; border-color:{accent}; min-width:68px; }}
QPushButton#primaryButton:hover {{ background:{fg}; border-color:{fg}; }}
QDialogButtonBox QPushButton,QMessageBox QPushButton {{ min-width:68px; }}
QWidget#findBar {{ background:{pane}; border:1px solid {border}; border-radius:6px; }}
QWidget#findBar QWidget#findResizeHandle {{ background:transparent; border:0; }}
QWidget#findBar QLabel {{ background:transparent; border:none; }}
QWidget#findBar QLineEdit {{ background:{bg}; padding:0 6px; min-height:30px; max-height:30px; border-radius:4px; }}
QWidget#findBar QToolButton {{ background:transparent; border:none; padding:0; min-width:28px; max-width:28px; min-height:28px; max-height:28px; }}
QWidget#findBar QToolButton:hover {{ background:{hover}; border:none; border-radius:4px; }}
QWidget#findBar QToolButton:pressed {{ background:{colors['selection']}; border:none; }}
QWidget#findBar QLineEdit QToolButton:checked {{ background:{accent}; color:{bg}; border-radius:3px; }}
QLabel#findFeedback {{ color:{muted}; font-size:14px; }}
QCheckBox {{ spacing:7px; background:transparent; }}
QCheckBox::indicator {{ width:16px; height:16px; border:1px solid {border};
    border-radius:3px; background:{bg}; }}
QCheckBox::indicator:checked {{ background:{accent}; border-color:{accent}; }}
QLabel#dialogTitle {{ font-size:21px; font-weight:600; }}
QLabel#dialogLabel {{ color:{muted}; }}
QLabel#fileExtension {{ color:{muted}; font-family:'Cascadia Code','Consolas'; font-size:12px; }}
QListWidget#documentTypeList {{ border:1px solid {border}; border-radius:6px; padding:5px; background:{bg}; }}
QListWidget#documentTypeList::item {{ border:1px solid transparent; padding:0; border-radius:4px; }}
QListWidget#documentTypeList::item:selected {{ border-color:{accent}; background:{hover}; }}
QWidget#documentTypeRow,QWidget#documentTypeRow QLabel {{ background:transparent; }}
QScrollBar:vertical {{ width:10px; background:{pane}; border-radius:4px; }}
QScrollBar::handle:vertical {{ background:{border}; min-height:26px; border-radius:4px; margin:2px; }}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {{ height:0; }}
QScrollBar:horizontal {{ height:10px; background:{pane}; border-radius:4px; }}
QScrollBar::handle:horizontal {{ background:{border}; min-width:26px; border-radius:4px; margin:2px; }}
QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal {{ width:0; }}
QToolTip {{ background:{pane}; color:{fg}; border:1px solid {border}; border-radius:4px; padding:5px; }}
'''


def apply_theme(window):
    app = QApplication.instance()
    if not hasattr(app, '_notepad_popup_filter'):
        app._notepad_popup_filter = PopupThemeFilter(app)
    colors = theme_colors(window.theme)
    palette = QPalette(window.palette())
    for role, key in ((QPalette.Window, 'bg'), (QPalette.Base, 'bg'), (QPalette.Button, 'pane'),
                      (QPalette.WindowText, 'fg'), (QPalette.Text, 'fg'), (QPalette.ButtonText, 'fg'),
                      (QPalette.Highlight, 'selection'), (QPalette.HighlightedText, 'fg'),
                      (QPalette.Light, 'border'), (QPalette.Midlight, 'border'), (QPalette.Dark, 'border')):
        palette.setColor(role, QColor(colors[key]))
    window.setPalette(palette)
    window.setStyleSheet(theme_stylesheet(window.theme))
    for menu in (window.settings_menu, window.theme_menu):
        style_popup(menu, window.theme)
    window.editor.set_theme(window.theme)
    icons = [(window.undo_action, QStyle.SP_ArrowBack), (window.redo_action, QStyle.SP_ArrowForward),
             (window.export_action, QStyle.SP_ArrowUp)]
    for control, standard in icons:
        icon = QIcon()
        for mode, color in ((QIcon.Normal, colors['fg']), (QIcon.Disabled, colors['muted'])):
            image = window.style().standardIcon(standard).pixmap(20, 20).toImage()
            painter = QPainter(image)
            painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
            painter.fillRect(image.rect(), QColor(color))
            painter.end()
            icon.addPixmap(QPixmap.fromImage(image), mode)
        control.setIcon(icon)
    for key, action in window.theme_actions.items():
        action.setChecked(key == window.theme)
    for key, button in {**window.header_buttons, **window.sidebar_buttons}.items():
        button.setIcon(control_icon(key, window.theme))
    for name, symbol in (
        ('find_previous_button', 'find-up'), ('find_next_button', 'find-down'),
        ('replace_button', 'replace'), ('replace_all_button', 'replace-all'),
        ('find_close_button', 'close'),
    ):
        getattr(window, name).setIcon(control_icon(symbol, window.theme))
    if hasattr(window, 'refresh_window_icons'):
        window.refresh_window_icons()
