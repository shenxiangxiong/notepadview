"""Debounced workspace snapshots. Only the UI thread accesses Scintilla objects."""
from copy import deepcopy
from pathlib import Path
from threading import Event, Thread

from PySide6.QtCore import QStandardPaths, QTimer
from PySide6.QtWidgets import QMessageBox

from notepadview.workspace_store import WorkspaceStore
from notepadview.errors import chinese_error


class WorkspaceController:
    def __init__(self, window, isolated=False):
        self.window = window
        path = (Path(window.settings.fileName() + '.workspace.json') if isolated else
                Path(QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation)) /
                'Notepad View' / 'workspace.json')
        self.store = WorkspaceStore(path.with_suffix('.sqlite3'))
        self.restoring = False
        self.closed = False
        self.load_error = None
        self._texts = {}
        self._work = None
        self._pending = False
        self._last_snapshot = None
        self.timer = QTimer(window)
        self.timer.setSingleShot(True)
        self.timer.setInterval(800)
        self.timer.timeout.connect(self.save_async)
        self.maximum_timer = QTimer(window)
        self.maximum_timer.setSingleShot(True)
        self.maximum_timer.setInterval(5000)
        self.maximum_timer.timeout.connect(self.save_async)
        self.poll = QTimer(window)
        self.poll.setInterval(80)
        self.poll.timeout.connect(self._poll)

    def restore(self):
        from notepadview.window import DocumentSession
        window = self.window
        self.restoring = True
        try:
            state = self.store.load(readonly_pdf=True)
            if state is None:
                return
            window.categories = deepcopy(state['categories'])
            for data in state['documents']:
                document = window._new_source_document(data['text'])
                document.setModified(data['modified'])
                session = DocumentSession(document=document, id=data['id'],
                                          path=Path(data['path']) if data['path'] else None,
                                          document_format=data['format'],
                                          manual_format=data.get('manual_format', False),
                                          untyped=data.get('untyped', False),
                                          format_suffix=data.get('format_suffix'),
                                          category_id=data['category_id'],
                                          selection=data['selection'],
                                          first_visible_line=data['first_visible_line'],
                                          preview_position=data.get('preview_position', 0),
                                          display_name=data.get('name', ''),
                                          kind=data.get('kind', 'text'),
                                          encoding=data.get('encoding', 'utf-8'),
                                          newline=data.get('newline', '\n'),
                                          preview_state=data.get('preview_state', {}),
                                          reader_state=data.get('reader_state', {}))
                session.refresh_text_format(data['text'])
                if session.kind == 'diagram':
                    window._attach_diagram(session, data['diagram_model'], data['modified'])
                if session.kind in ('docx', 'xlsx'):
                    from notepadview.office_editor import OfficeEditor
                    editor = (OfficeEditor.from_snapshot(data['office_edit'], window)
                              if data.get('office_edit') is not None else
                              OfficeEditor.from_path(session.path, window))
                    window._attach_image(session, editor)
                elif session.kind == 'pdf':
                    document.setModified(False)
                elif data.get('image_edit') is not None:
                    from notepadview.image_editor import ImageEditor
                    editor = ImageEditor.from_snapshot(data['image_edit'], window)
                    window._attach_image(session, editor)
                window.sessions.append(session)
                self._texts[session.id] = (document.revision, data['text'])
                window.document_tabs.blockSignals(True)
                index = window.document_tabs.addTab('')
                window.document_tabs.setTabData(index, session)
                window._refresh_document_tab(session)
                window.document_tabs.blockSignals(False)
            active = next((session for session in window.sessions if session.id == state['active_id']),
                          window.sessions[0] if window.sessions else None)
            if active is not None:
                window.document_tabs.setCurrentIndex(window._session_tab_index(active))
                if window.current_session is not active:
                    window._bind_document(active)
            window.refresh_file_list()
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.load_error = chinese_error(error)
            QTimer.singleShot(0, window, lambda: QMessageBox.critical(
                window, '无法恢复工作区', '缓存无法读取，已保留原缓存。请先备份缓存后再处理。\n\n'
                f'{self.store.path}\n{self.load_error}'))
        finally:
            self.restoring = False

    def schedule(self):
        if self.restoring or self.closed or self.load_error:
            return
        self.timer.start()
        if not self.maximum_timer.isActive():
            self.maximum_timer.start()

    def snapshot(self, exclude_ids=None):
        window = self.window
        current = window.current_session
        if current:
            current.selection = window.editor.selection_state()
            current.first_visible_line = window.editor.firstVisibleLine()
            current.preview_position = window.scroll_position
            if current.kind in ('image', 'pdf', 'office_pdf') and window.binary_preview.kind is not None:
                current.preview_state = window.binary_preview.state()
            elif current.kind == 'ebook' and current.ebook_widget is not None:
                current.reader_state = current.ebook_widget.state()
        documents = []
        active_ids = set()
        for session in window._tab_sessions():
            if exclude_ids and session.id in exclude_ids:
                continue
            active_ids.add(session.id)
            cached = self._texts.get(session.id)
            if cached is None or cached[0] != session.document.revision:
                text = bytes(session.document.get_char_range(0, session.document.length())).decode('utf-8')
                cached = (session.document.revision, text)
                self._texts[session.id] = cached
            documents.append({'id': session.id, 'path': str(session.path) if session.path else None,
                              'name': session.display_name,
                              'format': session.document_format, 'text': cached[1],
                              'manual_format': session.manual_format,
                              'untyped': session.untyped,
                              'format_suffix': session.format_suffix,
                              'modified': session.document.isModified(), 'category_id': session.category_id,
                              'selection': deepcopy(session.selection),
                              'first_visible_line': session.first_visible_line,
                              'preview_position': session.preview_position,
                              'kind': session.kind, 'encoding': session.encoding,
                              'newline': session.newline,
                              'preview_state': deepcopy(session.preview_state),
                              **({'reader_state': deepcopy(session.reader_state)}
                                 if session.kind == 'ebook' else {}),
                              **({('office_edit' if session.kind in ('docx', 'xlsx') else 'image_edit'): session.image_editor.snapshot()} if session.image_editor is not None else {}),
                              **({'diagram_model': deepcopy(session.diagram.model)} if session.kind == 'diagram' else {})})
        self._texts = {key: value for key, value in self._texts.items() if key in active_ids}
        active_id = current.id if current and current.id in active_ids else (documents[0]['id'] if documents else None)
        return {'version': 1, 'categories': deepcopy(window.categories), 'documents': documents,
                'active_id': active_id}

    def save_async(self):
        self.timer.stop()
        self.maximum_timer.stop()
        if self.closed or self.load_error:
            return
        if self._work is not None:
            self._pending = True
            return
        snapshot = self.snapshot()
        if snapshot == self._last_snapshot:
            return
        work = {'snapshot': snapshot, 'done': Event(), 'error': None}
        self._work = work
        thread = Thread(target=self._write, args=(work,), daemon=True)
        work['thread'] = thread
        thread.start()
        self.poll.start()

    def _write(self, work):
        try:
            self.store.save(work['snapshot'])
        except (OSError, ValueError, RuntimeError) as error:
            work['error'] = chinese_error(error)
        finally:
            work['done'].set()

    def _poll(self):
        if self._work is None or not self._work['done'].is_set():
            return
        work = self._work
        self._work = None
        self.poll.stop()
        if work['error']:
            self.window.statusBar().showMessage('工作区缓存失败：' + work['error'], 10000)
        else:
            self._last_snapshot = work['snapshot']
        if self._pending:
            self._pending = False
            self.schedule()

    def flush(self, exclude_ids=None):
        if self.closed:
            return True
        self.timer.stop()
        self.maximum_timer.stop()
        self.poll.stop()
        if self.load_error:
            QMessageBox.critical(self.window, '无法保存工作区',
                                 '为避免覆盖无法恢复的缓存，已停止自动保存。请先备份并修复缓存。\n\n' + self.load_error)
            return False
        if self._work is not None:
            self._work['thread'].join()
            self._work = None
        try:
            snapshot = self.snapshot(exclude_ids)
            self.store.save(snapshot)
            self._last_snapshot = snapshot
            self._pending = False
            return True
        except (OSError, ValueError, RuntimeError) as error:
            QMessageBox.critical(self.window, '缓存保存失败',
                                 '当前内容尚未安全缓存，暂未关闭窗口。请释放磁盘空间或使用“另存为”保存文档。\n\n' + chinese_error(error))
            return False

    def close(self):
        self.closed = True
        self.timer.stop()
        self.maximum_timer.stop()
        self.poll.stop()
        self.store.close()
