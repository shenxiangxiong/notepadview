"""Map visible preview characters to source selections without moving focus."""
import json
import re
from threading import Event, Thread
from bisect import bisect_left, bisect_right
from difflib import SequenceMatcher

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWebEngineCore import QWebEngineScript


SELECTION_SCRIPT = r'''
(() => {
    const nodes = [], groups = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node, offset = 0;
    while ((node = walker.nextNode())) {
        const element = node.parentElement.closest('[data-source-line]');
        if (!element || node.parentElement.closest('script,style')) continue;
        const text = node.textContent, length = Array.from(text).length;
        nodes.push({node, start:offset, end:offset+length});
        let group = groups[groups.length-1];
        if (!group || group.element !== element) {
            group = {element, line:Number(element.dataset.sourceLine),
                     end:element.dataset.sourceEnd ? Number(element.dataset.sourceEnd) : null,
                     text:'', start:offset};
            groups.push(group);
        }
        group.text += text;
        offset += length;
    }
    function position(container, offset) {
        const part = nodes.find(part => part.node === container);
        if (part) return part.start+Array.from(container.textContent.slice(0,offset)).length;
        const range = document.createRange(); range.setStart(container,offset); range.collapse(true);
        for (const part of nodes) {
            const other = document.createRange(); other.selectNodeContents(part.node);
            if (range.compareBoundaryPoints(Range.START_TO_START,other) <= 0) return part.start;
        }
        return nodes.length ? nodes[nodes.length-1].end : 0;
    }
    function endpoint(offset, end) {
        const part = nodes.find(part => end ? part.end >= offset && part.start < offset
                                           : part.end > offset && part.start <= offset);
        if (!part) return null;
        return [part.node,Array.from(part.node.textContent).slice(0,offset-part.start).join('').length];
    }
    globalThis.nvSelection = {
        read() {
            const selection = getSelection();
            if (!selection.rangeCount || selection.isCollapsed) return '[]';
            const range = selection.getRangeAt(0);
            return JSON.stringify([position(range.startContainer,range.startOffset),
                                   position(range.endContainer,range.endOffset)]);
        },
        select(start,end) {
            const selection = getSelection();
            selection.removeAllRanges();
            if (start === end) return;
            const a = endpoint(start,false), b = endpoint(end,true);
            if (!a || !b) return;
            const range = document.createRange(); range.setStart(...a); range.setEnd(...b);
            selection.addRange(range);
            document.dispatchEvent(new Event('selectionchange'));
        }
    };
    return JSON.stringify(groups.map(({line,end,text,start}) => ({line,end,text,start})));
})()
'''


class SelectionMap:
    def __init__(self, runs):
        self.runs = sorted(set(runs))
        self.starts = [a for a, _, _ in self.runs]
        self.ends = [a + size for a, _, size in self.runs]
        self.reverse = sorted((b, a, size) for a, b, size in self.runs)
        self.reverse_starts = [a for a, _, _ in self.reverse]
        self.reverse_ends = [a + size for a, _, size in self.reverse]

    def map_range(self, start, end, reverse=False):
        runs = self.reverse if reverse else self.runs
        starts = self.reverse_starts if reverse else self.starts
        ends = self.reverse_ends if reverse else self.ends
        first = bisect_right(ends, start)
        last = bisect_left(starts, end) - 1
        if first > last or start == end:
            return ()
        a, b, _ = runs[first]
        x, y, size = runs[last]
        return b + max(start - a, 0), y + min(end - x, size)


def character_pairs(source, groups):
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    pairs = []
    for group in groups:
        start = offsets[min(group['line'], len(lines))]
        end_line = group['end']
        end = offsets[min(end_line, len(lines))] if end_line is not None else len(source)
        text = group['text']
        if group['end'] is None:
            end = min(end, start + len(text) + text.count('\n'))
        original = source[start:end]
        normalized = original.replace('\r\n', '\n').replace('\r', '\n')
        if normalized.startswith(text) or text.startswith(normalized):
            limit = min(len(normalized), len(text))
            cursor = 0
            preview_offset = 0
            for newline in re.finditer(r'\r\n|\r', original):
                size = min(newline.start() - cursor, limit - preview_offset)
                if size > 0:
                    pairs.append((start + cursor, group['start'] + preview_offset, size))
                    preview_offset += size
                if preview_offset >= limit:
                    break
                pairs.append((start + newline.end() - 1, group['start'] + preview_offset, 1))
                preview_offset += 1
                cursor = newline.end()
            else:
                size = min(len(original) - cursor, limit - preview_offset)
                if size > 0:
                    pairs.append((start + cursor, group['start'] + preview_offset, size))
            continue
        if group['end'] is None:
            if text.startswith(original):
                text = original
        found = original.find(text)
        if found >= 0:
            if text:
                pairs.append((start + found, group['start'], len(text)))
        else:
            matcher = SequenceMatcher(None, original, text, autojunk=False)
            for match in matcher.get_matching_blocks():
                if match.size:
                    pairs.append((start + match.a, group['start'] + match.b, match.size))
    return SelectionMap(pairs)


class SelectionSync(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.mapping = SelectionMap([])
        self._work = None
        self._pending = None
        self.work_timer = QTimer(self)
        self.work_timer.setInterval(25)
        self.work_timer.timeout.connect(self._poll_work)
        self._generation = -1
        self._source_range = None
        self._preview_range = None
        self._reading = False
        self._writing = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self._from_source)
        window.editor.updateUi.connect(self._source_changed)
        window.editor.textChanged.connect(self._invalidate)
        self.preview_timer = QTimer(self)
        self.preview_timer.setInterval(100)
        self.preview_timer.timeout.connect(self._from_preview)
        window.preview.page().selectionChanged.connect(self._from_preview)
        self.preview_timer.start()

    def _invalidate(self):
        self._generation = -1
        self.timer.stop()

    def enabled(self):
        return self.window.scroll_sync.enabled() and self._generation == self.window._preview_generation

    def loaded(self):
        w = self.window
        self._generation = -1
        self.mapping = SelectionMap([])
        self.timer.stop()
        self._pending = None
        generation = w._preview_generation
        document = w.editor.document()
        revision = document.revision
        if not w.scroll_sync.enabled():
            return
        source = w.editor.source_text()

        def ready(value):
            if (generation != w._preview_generation or document is not w.editor.document()
                    or revision != document.revision or not value):
                return
            self._pending = dict(source=source, value=value, generation=generation,
                                 document=document, revision=revision, done=Event(), mapping=None)
            if self._work is None:
                self._start_work()

        w.preview.page().runJavaScript(SELECTION_SCRIPT, QWebEngineScript.ApplicationWorld, ready)

    def _start_work(self):
        self._work, self._pending = self._pending, None
        self.work_timer.start()
        Thread(target=self._build_mapping, args=(self._work,), daemon=True).start()

    @staticmethod
    def _build_mapping(work):
        try:
            work['mapping'] = character_pairs(work['source'], json.loads(work['value']))
        finally:
            work['done'].set()

    def _poll_work(self):
        work = self._work
        if work is None or not work['done'].is_set():
            return
        self.work_timer.stop()
        self._work = None
        w = self.window
        if (work['generation'] == w._preview_generation and work['document'] is w.editor.document()
                and work['revision'] == w.editor.document().revision and work['mapping'] is not None):
            self.mapping = work['mapping']
            self._generation = work['generation']
            self._source_range = None
            self._preview_range = ()
            w.preview.page().runJavaScript(
                'getSelection().removeAllRanges()', QWebEngineScript.ApplicationWorld)
            self._from_source()
        if self._pending is not None:
            self._start_work()

    def _source_changed(self, *_):
        if (self.enabled() and self.window.editor.selection_range() != self._source_range
                and not self.timer.isActive()):
            self.timer.start()

    def _from_source(self):
        if not self.enabled() or self._writing:
            return
        selected = self.window.editor.selection_range()
        if selected == self._source_range:
            return
        self._source_range = selected
        start, end = selected
        target = self.mapping.map_range(start, end)
        self._preview_range = target
        a, b = target if target else (0, 0)
        if not target and not self.window.preview.page().selectedText():
            return
        self._writing = True

        def written(_):
            self._writing = False
            self._source_changed()

        self.window.preview.page().runJavaScript(
            f'globalThis.nvSelection?.select({a},{b})', QWebEngineScript.ApplicationWorld, written)

    def _from_preview(self):
        if not self.enabled() or self._reading or self._writing:
            return
        if not self.window.preview.page().selectedText() and not self._preview_range:
            return
        generation = self._generation
        source_range = self.window.editor.selection_range()
        if source_range != self._source_range and not self.window.preview.hasFocus():
            self._source_changed()
            return
        self._reading = True

        def selected(value):
            self._reading = False
            if not self.enabled() or self._writing or generation != self._generation or not value:
                return
            if self.window.editor.selection_range() != source_range:
                self._source_changed()
                return
            target = tuple(json.loads(value))
            if target == self._preview_range:
                return
            self._preview_range = target
            if target:
                selection = self.mapping.map_range(*target, reverse=True)
                if not selection:
                    return
            else:
                start, _ = self.window.editor.selection_range()
                selection = (start, start)
            self._source_range = selection
            self.window.editor.set_selection(*selection)

        self.window.preview.page().runJavaScript(
            'globalThis.nvSelection?.read()', QWebEngineScript.ApplicationWorld, selected)
