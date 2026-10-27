"""Qt 原生图片与 PDF 只读预览。"""
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, Qt, QTimer
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QScrollArea,
                              QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

try:
    from PySide6.QtPdf import QPdfDocument
    from PySide6.QtPdfWidgets import QPdfView
except ImportError:
    QPdfDocument = QPdfView = None


def file_kind(path):
    with Path(path).open('rb') as stream:
        header = stream.read(1024)
    if header.startswith(b'%PDF-'):
        return 'pdf'
    reader = QImageReader(str(path))
    reader.setDecideFormatFromContent(True)
    if reader.canRead():
        return 'image'
    return 'text'


class BinaryPreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.kind = None
        self.path = None
        self.scale = 1.0
        self.fitted = True
        self.pixmap = QPixmap()
        self.pdf_document = None
        layout = QVBoxLayout(self)
        tools = QHBoxLayout()
        self.caption = QLabel('只读预览')
        tools.addWidget(self.caption)
        for label, callback in [('缩小', lambda: self.zoom(0.8)),
                                ('放大', lambda: self.zoom(1.25)), ('适应窗口', self.fit)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            tools.addWidget(button)
        self.previous = QPushButton('‹  上一页')
        self.next = QPushButton('下一页  ›')
        self.page_number = QSpinBox()
        self.page_number.setFixedWidth(78)
        self.page_number.setAlignment(Qt.AlignCenter)
        self.page_number.setButtonSymbols(QSpinBox.NoButtons)
        self.page_number.setToolTip('输入页码后按回车跳转')
        self.page_number.setAccessibleName('当前页码')
        self.page_number.setKeyboardTracking(False)
        self.total = QLabel()
        self.previous.clicked.connect(lambda: self.page_number.setValue(self.page_number.value() - 1))
        self.next.clicked.connect(lambda: self.page_number.setValue(self.page_number.value() + 1))
        self.page_number.valueChanged.connect(self._go_page)
        self.navigation = QWidget(self)
        self.navigation.setObjectName('pdfNavigation')
        navigation_layout = QHBoxLayout(self.navigation)
        navigation_layout.setContentsMargins(10, 6, 10, 6)
        navigation_layout.setSpacing(10)
        for widget in (self.previous, self.page_number, self.total, self.next):
            navigation_layout.addWidget(widget)
        tools.addStretch()
        layout.addLayout(tools)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_scroll = QScrollArea()
        self.image_scroll.setAlignment(Qt.AlignCenter)
        self.image_scroll.setWidget(self.image_label)
        self.stack.addWidget(self.image_scroll)
        self.pdf_view = QPdfView(self) if QPdfView is not None else None
        if self.pdf_view is not None:
            self.stack.addWidget(self.pdf_view)
            self.pdf_view.setPageMode(QPdfView.PageMode.SinglePage)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.addStretch()
        bottom.addWidget(self.navigation)
        bottom.addStretch()
        layout.addLayout(bottom)
        self.navigation.hide()

    def load(self, path, kind, state=None):
        state = state or {}
        pixmap = QPixmap()
        document = None
        if kind == 'image':
            reader = QImageReader(str(path))
            reader.setDecideFormatFromContent(True)
            reader.setAutoTransform(True)
            image = reader.read()
            if image.isNull():
                raise ValueError('无法解码图片：' + reader.errorString())
            pixmap = QPixmap.fromImage(image)
        else:
            if QPdfDocument is None:
                raise ValueError('当前 Qt 环境未提供 PDF 组件，请用系统默认程序打开。')
            document = QPdfDocument(self)
            buffer = QBuffer(document)
            buffer.setData(QByteArray(Path(path).read_bytes()))
            buffer.open(QIODevice.ReadOnly)
            document.load(buffer)
            if document.status() != QPdfDocument.Status.Ready or document.pageCount() == 0:
                document.deleteLater()
                raise ValueError('无法读取 PDF，文件可能损坏或需要密码。')
        self.release()
        self.kind, self.path = kind, Path(path)
        self.pixmap, self.pdf_document = pixmap, document
        self.caption.setText(('图片' if kind == 'image' else 'PDF') + ' · 只读预览')
        is_pdf = kind == 'pdf'
        self.navigation.setVisible(is_pdf)
        if is_pdf:
            self.pdf_view.setDocument(document)
            self.stack.setCurrentWidget(self.pdf_view)
            self.page_number.setRange(1, document.pageCount())
            self.total.setText('/ ' + str(document.pageCount()))
            self.page_number.setValue(min(document.pageCount(), int(state.get('page', 0)) + 1))
            self._go_page(self.page_number.value())
        else:
            self.stack.setCurrentWidget(self.image_scroll)
        self.scale = max(0.05, min(8.0, float(state.get('scale', 1.0))))
        self.fitted = True
        QTimer.singleShot(0, self, self.fit)
        self._apply_scale()

    def release(self):
        if self.pdf_document is not None:
            self.pdf_view.setDocument(None)
            self.pdf_document.close()
            self.pdf_document.deleteLater()
            self.pdf_document = None
        self.kind = None
        self.path = None
        self.pixmap = QPixmap()
        self.image_label.clear()

    def state(self):
        return {'scale': self.scale, 'fit': self.fitted,
                'page': self.pdf_view.pageNavigator().currentPage() if self.kind == 'pdf' else 0}

    def _go_page(self, number):
        if self.kind == 'pdf' and self.pdf_document is not None:
            self.pdf_view.pageNavigator().jump(number - 1, QPointF(), self.scale)
            self.previous.setEnabled(number > 1)
            self.next.setEnabled(number < self.pdf_document.pageCount())

    def zoom(self, factor):
        if self.kind == 'image' and self.fitted and not self.pixmap.isNull():
            self.scale = self.image_label.width() / self.pixmap.width()
        elif self.kind == 'pdf' and self.fitted:
            self.scale = self.pdf_view.zoomFactor()
        self.scale = max(0.05, min(8.0, self.scale * factor))
        self.fitted = False
        self._apply_scale()

    def fit(self):
        self.fitted = True
        self._apply_scale()

    def _apply_scale(self):
        if self.kind == 'pdf':
            self.pdf_view.setZoomMode(QPdfView.ZoomMode.FitInView if self.fitted else QPdfView.ZoomMode.Custom)
            if not self.fitted:
                self.pdf_view.setZoomFactor(self.scale)
        elif self.kind == 'image' and not self.pixmap.isNull():
            size = self.image_scroll.viewport().size() if self.fitted else self.pixmap.size() * self.scale
            scaled = self.pixmap.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.image_label.setPixmap(scaled)
            self.image_label.resize(scaled.size())

    def showEvent(self, event):
        super().showEvent(event)
        self.fit()
        QTimer.singleShot(0, self, self.fit)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fitted:
            self._apply_scale()
