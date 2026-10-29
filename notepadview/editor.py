"""Scintilla adapter. Public selection positions use Python character offsets."""
import logging
import json
import re

import sqlparse
from threading import Event, Thread

from PySide6.QtCore import Signal, QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog, QMessageBox, QWidget

from notepadview.theme import ThemedMenu as QMenu
from pyside6_scintilla import Scintilla, ScintillaDocument, ScintillaEdit
from pygments.lexers import get_lexer_by_name
from pygments.token import Token

from notepadview.palettes import theme_colors
from notepadview.search import BoundedSearch
from notepadview.syntax import style_for_theme
from notepadview.text_types import lexer_for


MAX_SYNC_HIGHLIGHT_BYTES = 64 * 1024
MAX_HIGHLIGHT_BYTES = 16 * 1024 * 1024


class SourceDocument(ScintillaDocument):
    modificationChanged = Signal(bool)
    undoAvailable = Signal(bool)
    redoAvailable = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.revision = 0
        self.highlight_key = None
        self.statistics_revision = -1
        self.statistics = (0, 0)
        self._restored_modified = False
        self.encoding_modified = False
        self.set_code_page(65001)
        self.modified.connect(self._modified)
        self.save_point.connect(self._notify)

    def _modified(self, flags, *_):
        if flags & (Scintilla.ModificationFlags.InsertText | Scintilla.ModificationFlags.DeleteText):
            self._notify()

    def _notify(self, *_):
        self.modificationChanged.emit(self.isModified())
        self.undoAvailable.emit(self.can_undo())
        self.redoAvailable.emit(self.can_redo())

    def setPlainText(self, text):
        self._restored_modified = False
        self.revision += 1
        self.set_undo_collection(False)
        try:
            self.delete_chars(0, self.length())
            self.insert_string(0, text.encode('utf-8'))
        finally:
            self.set_undo_collection(True)
        self.delete_undo_history()
        self.set_save_point()
        self._notify()

    def isModified(self):
        return self.encoding_modified or self._restored_modified or not self.is_save_point()

    def set_encoding_modified(self):
        # 编码元数据独立于正文保存点，撤销正文不能清除编码修改。
        self.encoding_modified = True
        self._notify()

    def setModified(self, modified):
        self._restored_modified = bool(modified)
        if not modified:
            self.encoding_modified = False
            self.set_save_point()
        self._notify()

    def isUndoAvailable(self):
        return self.can_undo()

    def isRedoAvailable(self):
        return self.can_redo()


class SourceEditor(ScintillaEdit):
    textChanged = Signal()
    cursorPositionChanged = Signal()
    TOKENS = (Token.Text, Token.Keyword, Token.Name.Tag, Token.Name.Attribute,
              Token.Name, Token.Literal.String, Token.Literal.Number,
              Token.Comment, Token.Operator, Token.Punctuation,
              Token.Generic.Heading, Token.Generic.Strong, Token.Generic.Emph,
              Token.Error)

    def __init__(self):
        super().__init__()
        self.language = 'md'
        self.dark = False
        self._styling = False
        self._pending_highlight = None
        self._highlight_work = None
        self._token_styles = {}
        self._last_empty_match = None
        self._doc = SourceDocument()
        self._owns_document = True
        self.set_doc(self._doc)
        self.setCodePage(65001)
        self.setTabWidth(4)
        self.setUseTabs(False)
        self.setMarginTypeN(0, Scintilla.MarginType.Number)
        self.setMarginWidthN(0, 54)
        self.setScrollWidthTracking(True)
        self.setScrollWidth(1)
        self.setMouseSelectionRectangularSwitch(True)
        self.setRectangularSelectionModifier(Scintilla.KeyMod.Alt)
        self.setVirtualSpaceOptions(Scintilla.VirtualSpace.RectangularSelection)
        self.setMultipleSelection(True)
        self.setAdditionalSelectionTyping(True)
        self.setMultiPaste(Scintilla.MultiPaste.Each)
        self.usePopUp(0)
        self.highlight_timer = QTimer(self)
        self.highlight_timer.setSingleShot(True)
        self.highlight_timer.setInterval(80)
        self.highlight_timer.timeout.connect(self.rehighlight)
        self.highlight_chunk_timer = QTimer(self)
        self.highlight_chunk_timer.setSingleShot(True)
        self.highlight_chunk_timer.timeout.connect(self._highlight_chunk)
        self.highlight_poll_timer = QTimer(self)
        self.highlight_poll_timer.setInterval(25)
        self.highlight_poll_timer.timeout.connect(self._poll_highlight_worker)
        self.modified.connect(self._on_modified)
        self.updateUi.connect(lambda *_: self.cursorPositionChanged.emit())
        self.set_theme(False)

    def document(self):
        return self._doc

    def build_context_menu(self):
        menu = QMenu(self)
        writable = not self.readOnly()
        selected = not self.selectionEmpty()
        commands = (
            ('撤销', self.undo, writable and self._doc.isUndoAvailable(), 'Ctrl+Z'),
            ('重做', self.redo, writable and self._doc.isRedoAvailable(), 'Ctrl+Y'),
            None,
            ('剪切', self.cut, writable and selected, 'Ctrl+X'),
            ('复制', self.copy, selected, 'Ctrl+C'),
            ('粘贴', self.paste, writable and self.canPaste(), 'Ctrl+V'),
            ('删除', lambda: ScintillaEdit.clear(self), writable and selected, None),
            None,
            ('全选', self.selectAll, self.length() > 0, 'Ctrl+A'),
        )
        for command in commands:
            if command is None:
                menu.addSeparator()
                continue
            label, callback, enabled, shortcut = command
            action = menu.addAction(label)
            action.triggered.connect(callback)
            action.setEnabled(enabled)
            if shortcut:
                action.setShortcut(shortcut)
                action.setShortcutVisibleInContextMenu(True)
        operations = self.format_operations()
        if operations:
            menu.addSeparator()
        ordinary = not self.selectionIsRectangle() and self.selections() == 1
        for label, operation in operations:
            action = menu.addAction(label)
            action.setEnabled(ordinary and self.length() > 0 and (writable or operation == 'check'))
            action.triggered.connect(lambda _=False, op=operation: self.apply_format_operation(op))
        return menu

    def format_operations(self):
        if self.language == 'md':
            return [(name + '级标题', 'heading' + str(level)) for level, name in
                    enumerate(('一', '二', '三', '四', '五', '六'), 1)] + [
                        ('正文', 'paragraph'), ('加粗', 'bold'), ('斜体', 'italic'),
                        ('列表', 'list'), ('引用', 'quote'), ('代码块', 'code')]
        if self.language == 'jsonl':
            return [('JSONL 安全格式化', 'format'), ('JSONL 语法检查', 'check')]
        if self.language in ('json', 'xml'):
            name = self.language.upper()
            return [(name + ' 格式化', 'format')] + (
                [(name + ' 压缩', 'compact')] if self.language == 'json' else []) + [
                    (name + ' 语法检查', 'check'), (name + ' 自动修复…', 'repair')]
        return {'sql': [('SQL 格式化', 'format'), ('SQL 关键字大写', 'upper'),
                        ('SQL 关键字小写', 'lower')],
                'txt': [('清理行尾空格', 'trim'), ('删除空行', 'blank')]}.get(self.language, [])

    def apply_format_operation(self, operation, whole_document=False):
        from notepadview.formatting import (
            compact_json, format_json, format_xml, format_sql, markdown_format,
            repair_json, repair_xml,
        )
        from notepadview.format_preview import RepairPreviewDialog
        if operation not in dict((op, label) for label, op in self.format_operations()):
            return False
        if self.readOnly() and operation != 'check':
            return False
        if self.selectionIsRectangle() or self.selections() != 1:
            return False
        source = self.source_text()
        start, end = self.selection_range()
        if whole_document or start == end:
            if self.language == 'md' and not whole_document:
                position = self.currentPos()
                line = self.lineFromPosition(position)
                start = self._char_offset(self.positionFromLine(line))
                end = self._char_offset(self.lineEndPosition(line))
            else:
                start, end = 0, len(source)
        original = source[start:end]
        newline = '\r\n' if '\r\n' in source else ('\r' if '\r' in source and '\n' not in source else '\n')
        document = self._doc
        revision = document.revision
        try:
            if self.language == 'md':
                result = markdown_format(original, operation, newline)
            elif self.language == 'txt':
                if operation == 'trim':
                    result = re.sub(r'[ \t]+(?=\r?$|\r)', '', original, flags=re.MULTILINE)
                else:
                    result = ''.join(line for line in original.splitlines(keepends=True) if line.strip())
            elif self.language == 'jsonl':
                from notepadview.formatting import format_jsonl
                result = format_jsonl(original)
                if operation == 'check':
                    QMessageBox.information(self, '语法检查', '语法检查通过。')
                    return True
            elif self.language == 'sql':
                result = (format_sql(original) if operation == 'format' else
                          sqlparse.format(original, keyword_case=operation))
            else:
                formatter = format_json if self.language == 'json' else format_xml
                if operation == 'repair':
                    # 有效输入无需修复或规范化。
                    try:
                        formatter(original)
                    except ValueError:
                        pass
                    else:
                        QMessageBox.information(self, '自动修复', '语法正确，无需修复。')
                        return True
                    result = formatter((repair_json if self.language == 'json' else repair_xml)(original))
                elif operation == 'compact':
                    result = compact_json(original)
                else:
                    result = formatter(original)
                if operation == 'check':
                    QMessageBox.information(self, '语法检查', '语法检查通过。')
                    return True
            if self.language in ('json', 'jsonl', 'xml', 'sql'):
                result = result.replace('\r\n', '\n').replace('\n', newline)
            if operation == 'repair':
                dialog = RepairPreviewDialog(original, result, self)
                try:
                    if dialog.exec() != QDialog.Accepted:
                        return False
                finally:
                    dialog.deleteLater()
            if self.readOnly() or self._doc is not document or document.revision != revision:
                return False
            if result != original:
                self.beginUndoAction()
                try:
                    self.setTargetRange(self._byte_offset(start), self._byte_offset(end))
                    self.replaceTarget(len(result.encode('utf-8')), result)
                    self.set_selection(start, start + len(result))
                finally:
                    self.endUndoAction()
                self._doc._notify()
                self.rehighlight()
            return True
        except (ValueError, RecursionError) as error:
            from notepadview.errors import chinese_error
            # 修复候选的坐标不一定对应原文；定位必须依据原始片段。
            position_error = error
            if self.language in ('json', 'xml'):
                try:
                    (format_json if self.language == 'json' else format_xml)(original)
                except ValueError as original_error:
                    position_error = original_error
            relative = None
            if isinstance(position_error, json.JSONDecodeError):
                relative = position_error.pos
            elif position_error.__cause__ is not None and hasattr(position_error.__cause__, 'lineno'):
                cause = position_error.__cause__
                lines = original.splitlines(keepends=True)
                relative = sum(map(len, lines[:cause.lineno - 1])) + cause.offset
            location = ''
            if relative is not None:
                offset = min(end, start + relative)
                self.set_selection(offset, offset)
                self.scrollCaret()
                row, column = self.cursor_position()
                location = f'\n文档位置：第 {row} 行，第 {column} 列。'
            QMessageBox.warning(self, '格式操作失败', chinese_error(error) + location +
                                '\n片段必须完整且语法可恢复，原文已保留。')
            return False

    def contextMenuEvent(self, event):
        menu = self.build_context_menu()
        try:
            menu.exec(event.globalPos())
        finally:
            menu.deleteLater()
        event.accept()

    def setFocus(self, reason=Qt.OtherFocusReason):
        QWidget.setFocus(self, reason)

    def set_source_document(self, document, plain_text=False, temporary=False, language=None):
        self._last_empty_match = None
        self.highlight_timer.stop()
        self.highlight_chunk_timer.stop()
        self._pending_highlight = None
        if self._highlight_work is not None:
            self._highlight_work['cancel'].set()
            self._highlight_work['restart'] = True
        previous = self._doc
        owned = self._owns_document
        self._doc = document
        self._owns_document = temporary
        self.set_doc(document)
        if previous is not document and owned:
            previous.deleteLater()
        self.set_language(language or ('txt' if plain_text else self.language))

    def selection_state(self):
        return {
            'mode': self.selectionMode(), 'main': self.mainSelection(),
            'rectangle': (self.rectangularSelectionAnchor(), self.rectangularSelectionCaret(),
                          self.rectangularSelectionAnchorVirtualSpace(), self.rectangularSelectionCaretVirtualSpace()),
            'ranges': [(self.selectionNAnchor(i), self.selectionNCaret(i),
                        self.selectionNAnchorVirtualSpace(i), self.selectionNCaretVirtualSpace(i))
                       for i in range(self.selections())],
        }

    def restore_selection_state(self, state):
        if not state:
            self.setSel(0, 0)
            return
        self.clearSelections()
        self.setSelectionMode(state['mode'])
        if state['mode'] in (Scintilla.SelectionMode.Rectangle, Scintilla.SelectionMode.Thin):
            anchor, caret, anchor_space, caret_space = state['rectangle']
            self.setRectangularSelectionAnchor(anchor)
            self.setRectangularSelectionCaret(caret)
            self.setRectangularSelectionAnchorVirtualSpace(anchor_space)
            self.setRectangularSelectionCaretVirtualSpace(caret_space)
        else:
            for i, (anchor, caret, anchor_space, caret_space) in enumerate(state['ranges']):
                if i == 0:
                    self.setSelection(caret, anchor)
                else:
                    self.addSelection(caret, anchor)
                self.setSelectionNAnchorVirtualSpace(i, anchor_space)
                self.setSelectionNCaretVirtualSpace(i, caret_space)
        self.setMainSelection(min(state['main'], self.selections() - 1))

    def _on_modified(self, flags, *_):
        if flags & (Scintilla.ModificationFlags.InsertText | Scintilla.ModificationFlags.DeleteText):
            self._last_empty_match = None
            self._doc.revision += 1
            self.highlight_chunk_timer.stop()
            self._pending_highlight = None
            if self._highlight_work is not None:
                self._highlight_work['cancel'].set()
                self._highlight_work['restart'] = True
            self.highlight_timer.start()
            self.textChanged.emit()

    def set_language(self, language):
        self.language = language
        # Code and rectangular selections remain aligned with horizontal scrolling.
        self.setWrapMode(Scintilla.Wrap.Word if language in ('md', 'txt') else Scintilla.Wrap.None_)
        self.rehighlight()

    def set_plain_text_mode(self, enabled):
        self.set_language('txt' if enabled else 'md')

    def source_text(self):
        return bytes(self.getText(self.length() + 1))[:self.length()].decode('utf-8')

    def toPlainText(self):
        return self.source_text()

    def setPlainText(self, text):
        self._doc.setPlainText(text)
        self.rehighlight()

    def insertPlainText(self, text):
        self.replace_selection(text)

    def clear(self):
        self.setPlainText('')

    def _byte_offset(self, offset):
        return self.positionRelative(0, max(0, offset))

    def _char_offset(self, offset):
        return self.countCharacters(0, min(self.length(), max(0, offset)))

    def set_selection(self, start, end):
        self.setSel(self._byte_offset(start), self._byte_offset(end))

    def selection_range(self):
        return self._char_offset(self.selectionStart()), self._char_offset(self.selectionEnd())

    def selected_text(self):
        return bytes(self.getSelText()).rstrip(b'\0').decode('utf-8')

    def replace_selection(self, text):
        self.replaceSel(text)
        self._doc._notify()

    def replace_all_text(self, text):
        position = self._char_offset(self.currentPos())
        self.beginUndoAction()
        try:
            self.setTargetStart(0)
            self.setTargetEnd(self.length())
            data = text.encode('utf-8')
            self.replaceTarget(len(data), text)
        finally:
            self.endUndoAction()
        self.set_selection(min(position, len(text)), min(position, len(text)))
        self._doc._notify()
        self.rehighlight()

    def goto_line(self, line):
        self.gotoLine(line)
        self.verticalCentreCaret()

    def cursor_position(self):
        position = self.currentPos()
        line = self.lineFromPosition(position)
        return line + 1, self.countCharacters(self.positionFromLine(line), position) + 1

    def _empty_match_key(self, term):
        return self._doc, self._doc.revision, self.selection_range(), term

    def _empty_match_state(self, term):
        state = self._last_empty_match
        return state if state is not None and state[0] == self._empty_match_key(term) else None

    def _remember_empty_match(self, term, consumed=False):
        self._last_empty_match = self._empty_match_key(term), consumed

    def find_text(self, term, backward=False, regular_expression=False,
                  case_sensitive=False, whole_word=False):
        if not term:
            return False
        if regular_expression or case_sensitive or whole_word:
            operation = BoundedSearch(term, regular_expression,
                                      case_sensitive=case_sensitive, whole_word=whole_word)
            text = self.source_text()
            start, end = self.selection_range()
            match = operation.find(text, start, end, backward,
                                   self._empty_match_state(term) is not None)
            if match is None:
                self._last_empty_match = None
                return False
            operation.remaining()
            self.set_selection(*match.span())
            self._last_empty_match = None
            if match.start() == match.end():
                self._remember_empty_match(term)
            self.scrollCaret()
            return True
        self._last_empty_match = None
        start, end = self.selectionStart(), self.selectionEnd()
        ranges = ((start, 0), (self.length(), start)) if backward else ((end, self.length()), (0, end))
        self.setSearchFlags(0)
        data = term.encode('utf-8')
        for lower, upper in ranges:
            self.setTargetRange(lower, upper)
            if self.searchInTarget(len(data), term) >= 0:
                self.setSel(self.targetStart(), self.targetEnd())
                break
        else:
            return False
        self.scrollCaret()
        return True

    def replace_match(self, term, replacement, regular_expression=False,
                      case_sensitive=False, whole_word=False):
        if not term:
            return False
        if not (regular_expression or case_sensitive or whole_word):
            self._last_empty_match = None
            if self.selected_text().casefold() != term.casefold() and not self.find_text(term):
                return False
            self.replace_selection(replacement)
            return True
        operation = BoundedSearch(term, regular_expression, replacement,
                                  case_sensitive=case_sensitive, whole_word=whole_word)
        text = self.source_text()
        start, end = self.selection_range()
        empty_state = self._empty_match_state(term)
        match = None
        if start != end or (empty_state is not None and not empty_state[1]):
            match = operation.selected_match(text, start, end)
        if match is None:
            match = operation.find(text, start, end, skip_empty=empty_state is not None)
        if match is None:
            return False
        value = operation.expand(match, replacement)
        operation.remaining()
        self.set_selection(*match.span())
        self.replace_selection(value)
        self._last_empty_match = None
        if match.start() == match.end():
            self._remember_empty_match(term, consumed=True)
        return True

    def replace_all_matches(self, term, replacement, regular_expression=False,
                            case_sensitive=False, whole_word=False):
        if not term:
            return 0
        operation = BoundedSearch(term, regular_expression, replacement,
                                  case_sensitive=case_sensitive, whole_word=whole_word)
        source, count = operation.replace_all(self.source_text(), replacement)
        operation.remaining()
        if count:
            self.replace_all_text(source)
            self._last_empty_match = None
        return count

    def undo(self):
        super().undo()
        self._doc._notify()

    def redo(self):
        super().redo()
        self._doc._notify()

    def scroll_to_top(self):
        self.setFirstVisibleLine(0)

    def scroll_to_bottom(self):
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def set_theme(self, theme):
        if isinstance(theme, bool):
            theme = 'dark' if theme else 'light'
        self.dark = theme in ('dark', 'nv')
        colors = theme_colors(theme)
        palette = style_for_theme(theme)
        background = colors['bg']
        foreground = colors['fg']
        self.styleSetFont(Scintilla.StylesCommon.Default, 'Cascadia Code')
        self.styleSetSize(Scintilla.StylesCommon.Default, 11)
        self.styleSetBack(Scintilla.StylesCommon.Default, self._color(background))
        self.styleSetFore(Scintilla.StylesCommon.Default, self._color(foreground))
        self.styleClearAll()
        for index, token in enumerate(self.TOKENS):
            style = palette.style_for_token(token)
            self.styleSetFore(index, self._color('#' + style['color'] if style['color'] else foreground))
            self.styleSetBold(index, style['bold'])
            self.styleSetItalic(index, style['italic'])
        self.styleSetBack(Scintilla.StylesCommon.LineNumber, self._color(colors['pane']))
        self.styleSetFore(Scintilla.StylesCommon.LineNumber, self._color(colors['muted']))
        self.setCaretFore(self._color(foreground))
        self.setSelBack(True, self._color(colors['selection']))
        self.setAdditionalSelBack(self._color(colors['selection']))
        self.rehighlight()

    @staticmethod
    def _color(value):
        color = QColor(value)
        return color.red() | color.green() << 8 | color.blue() << 16

    def rehighlight(self):
        self.highlight_timer.stop()
        key = (self._doc.revision, self.language)
        if self._styling or self._doc.highlight_key == key:
            return
        if self._highlight_work is not None:
            work = self._highlight_work
            if work['document'] is self._doc and work['key'] == key and not work['cancel'].is_set():
                return
            work['cancel'].set()
            work['restart'] = True
            return
        self.highlight_chunk_timer.stop()
        self._pending_highlight = None
        if self.language == 'txt' or self.length() > MAX_HIGHLIGHT_BYTES:
            self.startStyling(0, 0)
            self.setStyling(self.length(), 0)
            self._doc.highlight_key = key
        elif self.length() > MAX_SYNC_HIGHLIGHT_BYTES:
            self._start_highlight_worker(key)
        else:
            lexer = lexer_for(self.language, stripnl=False, ensurenl=False)
            styles = bytearray()
            for _, token, value in lexer.get_tokens_unprocessed(self.source_text()):
                styles.extend(bytes((self._token_style(token),)) * len(value.encode('utf-8')))
            self._write_styles(0, styles)
            self._doc.highlight_key = key

    def _start_highlight_worker(self, key):
        work = {'document': self._doc, 'key': key, 'language': self.language,
                'source': self.source_text(), 'cancel': Event(), 'done': Event(),
                'restart': False, 'styles': None}
        self._highlight_work = work
        self.highlight_poll_timer.start()
        Thread(target=self._tokenize_highlight, args=(work,), daemon=True).start()

    @classmethod
    def _tokenize_highlight(cls, work):
        try:
            lexer = lexer_for(work['language'], stripnl=False, ensurenl=False)
            styles = bytearray()
            token_styles = {}
            for _, token, value in lexer.get_tokens_unprocessed(work['source']):
                if work['cancel'].is_set():
                    return
                if token not in token_styles:
                    categories = [category for category in cls.TOKENS if token in category]
                    category = max(categories, key=len) if categories else Token.Text
                    token_styles[token] = cls.TOKENS.index(category)
                styles.extend(bytes((token_styles[token],)) * len(value.encode('utf-8')))
            work['styles'] = styles.decode('ascii')
        except Exception:
            logging.getLogger(__name__).exception('Syntax highlighting failed')
        finally:
            work['done'].set()

    def _poll_highlight_worker(self):
        work = self._highlight_work
        if work is None or not work['done'].is_set():
            return
        self.highlight_poll_timer.stop()
        self._highlight_work = None
        if (work['document'] is self._doc and work['key'] == (self._doc.revision, self.language)
                and work['styles'] is not None):
            self._pending_highlight = [work['document'], work['key'], work['styles'], 0]
            self._highlight_chunk()
        elif work['restart'] or work['key'] != (self._doc.revision, self.language):
            self.rehighlight()

    def _token_style(self, token):
        if token not in self._token_styles:
            categories = [t for t in self.TOKENS if token in t]
            category = max(categories, key=len) if categories else Token.Text
            self._token_styles[token] = self.TOKENS.index(category)
        return self._token_styles[token]

    def _write_styles(self, position, styles):
        self._styling = True
        try:
            self.startStyling(position, 0)
            # This binding accepts str, but rejects bytes despite its annotation.
            self.setStylingEx(len(styles), styles if isinstance(styles, str) else styles.decode('ascii'))
        finally:
            self._styling = False

    def _highlight_chunk(self):
        pending = self._pending_highlight
        if pending is None:
            return
        document, key, styles, position = pending
        if document is not self._doc or key != (document.revision, self.language):
            self._pending_highlight = None
            self.highlight_timer.start()
            return
        end = min(position + 65536, len(styles))
        self._write_styles(position, styles[position:end])
        pending[3] = end
        if end >= len(styles):
            document.highlight_key = key
            self._pending_highlight = None
        else:
            self.highlight_chunk_timer.start(1)

    def closeEvent(self, event):
        self.highlight_timer.stop()
        self.highlight_chunk_timer.stop()
        self.highlight_poll_timer.stop()
        if self._highlight_work is not None:
            self._highlight_work['cancel'].set()
        self._pending_highlight = None
        super().closeEvent(event)

