import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QFont, QIcon, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QFileDialog, QHBoxLayout, QLabel,
                              QLineEdit, QMessageBox, QProgressBar, QPushButton, QVBoxLayout, QWidget)

from packaging_tools.installer_core import (APP_NAME, EXECUTABLE_NAME, INSTALL_DIR_NAME,
                                           default_install_target, install_payload, register_install)
from notepadview.errors import chinese_error
from notepadview.localization import install_chinese_translations
from notepadview.theme import theme_stylesheet

RESOURCES = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
PRODUCT_NAME = APP_NAME


class ChineseHelpFormatter(argparse.HelpFormatter):
    def _format_usage(self, usage, actions, groups, prefix):
        return super()._format_usage(usage, actions, groups, prefix or '用法：')


class ChineseArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        if message.startswith('unrecognized arguments: '):
            message = '无法识别的参数：' + message.removeprefix('unrecognized arguments: ')
        elif match := re.fullmatch(r'argument (.+): expected one argument', message):
            message = f'参数 {match[1]} 缺少取值，请指定安装目录。'
        elif match := re.fullmatch(r'argument (.+): ignored explicit argument .+', message):
            message = f'参数 {match[1]} 不接受取值。'
        elif match := re.fullmatch(r'ambiguous option: (.+) could match (.+)', message):
            message = f'参数 {match[1]} 不明确，请使用完整参数名：{match[2]}'
        elif not any('\u4e00' <= character <= '\u9fff' for character in message):
            message = '参数无效，请检查参数名称及其取值。'
        self.print_usage(sys.stderr)
        self.exit(2, f'{self.prog}: 错误：{message}\n')


class InstallWorker(QThread):
    progress = Signal(int)
    finished_install = Signal(str)

    def __init__(self, target, desktop, already_installed=False):
        super().__init__()
        self.target, self.desktop = target, desktop
        self.files_installed = already_installed

    def run(self):
        try:
            if not self.files_installed:
                install_payload(RESOURCES / 'payload.tar.xz', self.target, self.progress.emit)
                self.files_installed = True
            register_install(self.target, self.desktop)
            self.progress.emit(100)
            self.finished_install.emit('')
        except Exception as error:
            self.finished_install.emit(chinese_error(error))


class SetupWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('安装 ' + PRODUCT_NAME)
        self.setWindowIcon(QIcon(str(RESOURCES / 'notepadview' / 'assets' / 'app.ico')))
        self.setMinimumWidth(550)
        self.setStyleSheet(theme_stylesheet('light') + '''QWidget { background:#ffffff; color:#272b30; font-family:'Microsoft YaHei UI'; font-size:13px; }
            QLineEdit { padding:9px; border:1px solid #d4dadf; border-radius:4px; }
            QPushButton { padding:9px 18px; border:1px solid #d4dadf; border-radius:4px; }
            QPushButton#primary { background:#087f68; color:white; border:0; }
            QProgressBar { border:1px solid #d4dadf; border-radius:4px; text-align:center; height:22px; }
            QProgressBar::chunk { background:#087f68; }''')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 30, 32, 28)
        layout.setSpacing(18)
        header = QHBoxLayout()
        header.setSpacing(14)
        self.logo = QLabel()
        self.logo.setFixedSize(48, 48)
        self.logo.setPixmap(QPixmap(str(RESOURCES / 'notepadview' / 'assets' / 'app.png')).scaled(
            48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        title = QLabel(PRODUCT_NAME)
        title.setStyleSheet('font-size:28px; font-weight:700; color:#087f68;')
        header.addWidget(self.logo)
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)
        layout.addWidget(QLabel('通用文档编辑器  ·  版本 1.0.1'))
        layout.addSpacing(10)
        layout.addWidget(QLabel('安装位置'))
        row = QHBoxLayout()
        self.location = QLineEdit(str(default_install_target()))
        row.addWidget(self.location)
        self.browse = QPushButton('浏览…')
        self.browse.clicked.connect(self.choose_directory)
        row.addWidget(self.browse)
        layout.addLayout(row)
        self.desktop = QCheckBox('创建桌面快捷方式')
        self.desktop.setChecked(True)
        layout.addWidget(self.desktop)
        self.progress = QProgressBar()
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.status = QLabel('安装到当前用户，无需管理员权限。')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.button = QPushButton('安装')
        self.button.setObjectName('primary')
        self.button.clicked.connect(self.install)
        layout.addWidget(self.button)
        self.worker = None
        self.complete = False
        self.files_installed = False

    def choose_directory(self):
        path = QFileDialog.getExistingDirectory(self, '选择安装位置', self.location.text(),
                                                options=QFileDialog.DontUseNativeDialog)
        if path:
            self.location.setText(str(Path(path) / INSTALL_DIR_NAME))

    def install(self):
        if self.complete:
            try:
                subprocess.Popen([str(Path(self.location.text()) / EXECUTABLE_NAME)], cwd=self.location.text())
            except Exception as error:
                self.status.setText('启动失败：' + chinese_error(error))
                return
            self.close()
            return
        if not self.location.text().strip():
            return
        self.button.setEnabled(False)
        self.location.setEnabled(False)
        self.browse.setEnabled(False)
        self.desktop.setEnabled(False)
        self.status.setText('正在安装…')
        self.worker = InstallWorker(Path(self.location.text()), self.desktop.isChecked(), self.files_installed)
        self.worker.progress.connect(self.progress.setValue)
        self.worker.finished_install.connect(self.finished)
        self.worker.start()

    def finished(self, error):
        self.button.setEnabled(True)
        self.files_installed = self.worker.files_installed
        if error:
            self.status.setText('安装未完成：' + error)
            if self.files_installed:
                self.button.setText('重试注册')
            else:
                self.location.setEnabled(True)
                self.browse.setEnabled(True)
                self.desktop.setEnabled(True)
        else:
            self.complete = True
            self.status.setText('安装完成。可从桌面或开始菜单打开，也可在系统“已安装的应用”中卸载。')
            self.button.setText('启动 Notepad View')

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            event.ignore()
        else:
            event.accept()


def main():
    parser = ChineseArgumentParser(add_help=False, formatter_class=ChineseHelpFormatter,
                                   description='安装 Notepad View。')
    parser._optionals.title = '选项'
    parser.add_argument('-h', '--help', action='help', help='显示帮助并退出')
    parser.add_argument('--silent', action='store_true', help='静默安装，不显示安装窗口')
    parser.add_argument('--dir', type=Path, metavar='安装目录', help='指定安装目录')
    parser.add_argument('--no-register', action='store_true', help='跳过卸载信息和快捷方式注册')
    args = parser.parse_args()
    if args.silent:
        if not args.dir:
            parser.error('--silent 必须同时指定 --dir 安装目录。')
        try:
            install_payload(RESOURCES / 'payload.tar.xz', args.dir)
            if not args.no_register:
                register_install(args.dir)
        except Exception as error:
            parser.exit(1, '安装失败：' + chinese_error(error) + '\n')
        return
    app = QApplication(sys.argv)
    install_chinese_translations(app)
    app.setStyle('Fusion')
    app.setFont(QFont('Microsoft YaHei UI', 10))
    window = SetupWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
