"""Keep standard Qt buttons and file dialogs in Simplified Chinese."""
from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
from pathlib import Path


class ChineseTranslator(QTranslator):
    BUTTONS = {'OK': '确定', 'Save': '保存', 'Save All': '全部保存', 'Open': '打开',
               'Cancel': '取消', 'Close': '关闭', 'Discard': '不保存', 'Yes': '是',
               'Yes to All': '全部是', 'No': '否', 'No to All': '全部否',
               'Apply': '应用', 'Reset': '重置', 'Restore Defaults': '恢复默认',
               'Help': '帮助', 'Abort': '终止', 'Retry': '重试', 'Ignore': '忽略'}

    def translate(self, context, source_text, disambiguation=None, n=-1):
        if context in ('QPlatformTheme', 'QDialogButtonBox', 'QMessageBox', 'QFileDialog'):
            translated = self.BUTTONS.get(source_text.replace('&', ''))
            if translated:
                return translated
        return super().translate(context, source_text, disambiguation, n)


def install_chinese_translations(app):
    if getattr(app, '_notepad_translator', None) is not None:
        return
    QLocale.setDefault(QLocale(QLocale.Chinese, QLocale.China))
    translator = ChineseTranslator(app)
    bundled = Path(__file__).resolve().parent / 'assets' / 'qtbase_zh_CN.qm'
    loaded = translator.load(str(bundled)) if bundled.is_file() else False
    if not loaded and not translator.load('qtbase_zh_CN', QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
        raise RuntimeError('缺少 Qt 简体中文翻译文件 qtbase_zh_CN.qm')
    app.installTranslator(translator)
    app._notepad_translator = translator
