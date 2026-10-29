"""Bidirectional source/preview scrolling in the isolated Chromium world."""
import json

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWebEngineCore import QWebEngineScript


MAPPING_SCRIPT = r'''
(() => {
    const blocks = [...document.querySelectorAll('[data-source-line]')];
    const entries = [];
    for (const element of blocks) {
        const start = Number(element.dataset.sourceLine);
        if (element.dataset.sourceText) {
            const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
            const nodes = []; let node, length = 0;
            while ((node = walker.nextNode())) {
                nodes.push({node, start:length, end:length + node.length});
                length += node.length;
            }
            const text = element.textContent;
            const offsets = [0];
            for (let i = 0; i < text.length; i++) if (text[i] === '\n' && i + 1 < text.length) offsets.push(i + 1);
            offsets.forEach((offset, i) => entries.push({line:start+i, element, nodes, offset}));
        } else entries.push({line:start, element});
    }
    entries.sort((a,b) => a.line-b.line);
    const unique = entries.filter((entry, i) => !i || entry.line !== entries[i-1].line);
    function top(entry) {
        if (entry.nodes) {
            const part = entry.nodes.find(n => n.end > entry.offset);
            if (part) {
                const range = document.createRange();
                range.setStart(part.node, entry.offset-part.start);
                range.setEnd(part.node, Math.min(part.node.length, entry.offset-part.start+1));
                const rect = range.getBoundingClientRect();
                if (rect.height) return rect.top + scrollY;
            }
        }
        return entry.element.getBoundingClientRect().top + scrollY;
    }
    globalThis.nvScrollMap = {
        toY(line) {
            if (!unique.length) return 0;
            let lo = 0, hi = unique.length-1;
            while (lo < hi) {
                const mid = Math.ceil((lo+hi)/2);
                if (unique[mid].line <= line) lo = mid; else hi = mid-1;
            }
            const a = unique[lo], b = unique[lo+1];
            return b ? top(a)+(top(b)-top(a))*Math.max(0,(line-a.line)/(b.line-a.line)) : top(a);
        },
        toLine(y) {
            if (!unique.length) return 0;
            let lo = 0, hi = unique.length-1;
            while (lo < hi) {
                const mid = Math.ceil((lo+hi)/2);
                if (top(unique[mid]) <= y) lo = mid; else hi = mid-1;
            }
            const a = unique[lo], b = unique[lo+1];
            return b && top(b)>top(a) ? a.line+(b.line-a.line)*Math.max(0,(y-top(a))/(top(b)-top(a))) : a.line;
        }
    };
    return unique.length;
})()
'''


class ScrollSync(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._busy = False
        self._applying_source = False
        self._serial = 0
        self._last_preview_y = None
        self._direction = None
        self._pending_direction = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(35)
        self.timer.timeout.connect(self._flush)
        window.editor.verticalScrollBar().valueChanged.connect(self._source_changed)
        window.preview.page().scrollPositionChanged.connect(self._preview_changed)

    def enabled(self):
        w = self.window
        return (w.mode == 'split' and not w.is_readonly_preview
                and w.new_document_format != 'html' and w._preview_ready)

    def loaded(self):
        self.reset()
        self.window.preview.page().runJavaScript(MAPPING_SCRIPT, QWebEngineScript.ApplicationWorld)

    def reset(self):
        self._serial += 1
        self._busy = False
        self._last_preview_y = None
        self._pending_direction = None
        self.timer.stop()

    def _source_changed(self, _value):
        if self._applying_source:
            return
        if self.enabled() and self._busy and self._direction == 'preview':
            self._serial += 1
            self._busy = False
            self._pending_direction = None
        self._queue('source')

    def _preview_changed(self, pos):
        if self._last_preview_y is not None and abs(pos.y()-self._last_preview_y) < 1:
            return
        self._last_preview_y = pos.y()
        if self._busy and self._direction == 'source':
            return
        self._queue('preview')

    def _queue(self, direction):
        if not self.enabled():
            return
        if self._busy:
            self._pending_direction = direction
            return
        if self.timer.isActive() and self._direction == 'source' and direction == 'preview':
            return
        self._direction = direction
        self.timer.start()

    def _flush(self):
        if not self.enabled() or self._busy:
            return
        w = self.window
        editor = w.editor
        self._busy = True
        self._serial += 1
        serial = self._serial
        generation = w._preview_generation
        direction = self._direction
        if direction == 'source':
            visible = editor.firstVisibleLine()
            line = editor.docLineFromVisible(visible)
            bar = editor.verticalScrollBar()
            bottom = bar.maximum() > 0 and bar.value() >= bar.maximum()
            script = (f'globalThis.nvScrollSyncSerial = {serial}; '
                      f'if (globalThis.nvScrollMap) window.scrollTo(0, '
                      + ('document.documentElement.scrollHeight' if bottom else f'nvScrollMap.toY({line})') + ');')
            if bottom:
                # 离屏块进入视口后高度会变化，等待布局稳定再对齐底部。
                script += (f'const generation = {generation}; let frames = 8; '
                           'function settleBottom() {'
                           f'if (globalThis.nvScrollSyncSerial !== {serial} || '
                           'Number(document.documentElement.dataset.previewGeneration) !== generation) return;'
                           'const last = document.body.lastElementChild;'
                           'if (last) last.scrollIntoView({block:"end"});'
                           'window.scrollTo(0, document.documentElement.scrollHeight);'
                           'if (--frames > 0) requestAnimationFrame(settleBottom);'
                           '} if (document.querySelector("pre.highlight")) requestAnimationFrame(settleBottom);')
        else:
            script = ('globalThis.nvScrollMap ? JSON.stringify([nvScrollMap.toLine(scrollY), '
                      'scrollY+innerHeight >= document.documentElement.scrollHeight-2]) : null')

        def finished(result):
            if serial != self._serial:
                return
            if self.enabled() and generation == w._preview_generation and direction == 'preview' and result:
                result = json.loads(result)
                line = max(0, min(editor.lineCount()-1, int(result[0])))
                self._applying_source = True
                try:
                    if result[1]:
                        editor.verticalScrollBar().setValue(editor.verticalScrollBar().maximum())
                    else:
                        visible = editor.visibleFromDocLine(line)
                        editor.setFirstVisibleLine(visible)
                finally:
                    self._applying_source = False
            QTimer.singleShot(100, lambda: self._release(serial))

        w.preview.page().runJavaScript('(() => {' + script + '})()' if direction == 'source'
                                      else script, QWebEngineScript.ApplicationWorld, finished)

    def _release(self, serial):
        if serial == self._serial:
            self._busy = False
            pending = self._pending_direction
            self._pending_direction = None
            if pending:
                self._queue(pending)
