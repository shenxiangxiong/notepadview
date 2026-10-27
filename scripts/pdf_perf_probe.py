"""Synthetic PDF interaction benchmark; run from the project root."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sys
sys.path.insert(0, os.getcwd())
import json
import time
import tracemalloc
import random
from pathlib import Path
from collections import Counter
from unittest.mock import patch
import pymupdf as fitz
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPointF
from notepadview.pdf_model import PdfModel
from notepadview.pdf_editor import PdfEditor


def run(folder):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'synthetic.pdf'
    if not path.exists():
        rng = random.Random(42)
        with fitz.open() as doc:
            for i in range(80):
                page = doc.new_page(width=600, height=800)
                page.insert_text((30, 40), f'Page {i} original text')
                pix = fitz.Pixmap(fitz.csRGB, 512, 512, rng.randbytes(512*512*3), False)
                page.insert_image(fitz.Rect(30, 180, 570, 720), pixmap=pix)
            doc.save(path)
    app = QApplication.instance() or QApplication([])
    counts = Counter()
    times = {}
    original_export, original_open = PdfModel.export, fitz.open
    def export(self):
        counts['exports'] += 1
        return original_export(self)
    def opening(*args, **kwargs):
        counts['opens'] += 1
        return original_open(*args, **kwargs)
    def measure(name, callback):
        before = counts.copy()
        start = time.perf_counter()
        result = callback()
        times[name] = {'seconds': round(time.perf_counter()-start, 4), 'calls': dict(counts-before)}
        return result
    tracemalloc.start()
    with patch.object(PdfModel, 'export', export), patch.object(fitz, 'open', opening):
        editor = measure('open', lambda: PdfEditor.from_path(path))
        measure('turn_5', lambda: [editor.go_page(i) for i in range(1, 6)])
        measure('select_5', lambda: [editor.select_at(QPointF(10, 10)) for _ in range(5)])
        measure('zoom_5', lambda: [editor.zoom(1.1) for _ in range(5)])
        measure('edit_3', lambda: [editor.add_text(f'Added {i}', 40, 80+i*25) for i in range(3)])
        state = measure('snapshot', editor.snapshot)
        encoded = measure('serialize', lambda: json.dumps(state))
        measure('undo_redo', lambda: (editor.undo_stack.undo(), editor.undo_stack.redo()))
        measure('save', lambda: editor.save(folder / 'saved.pdf'))
        restored = measure('restore', lambda: PdfEditor.from_snapshot(state))
        assert restored.model.page_count == 80
        from notepadview.workspace_store import WorkspaceStore
        workspace = {'version': 1, 'categories': [], 'active_id': 'pdf',
                     'documents': [{'id': 'pdf', 'path': str(path.resolve()), 'format': 'txt', 'kind': 'pdf',
                                    'text': '', 'modified': True, 'category_id': '',
                                    'selection': None, 'first_visible_line': 0}]}
        workspace['documents'][0]['image_edit'] = state
        store = WorkspaceStore(folder / 'workspace.sqlite3')
        measure('persist_initial', lambda: store.save(workspace))
        workspace['documents'][0]['image_edit'] = editor.snapshot()
        workspace['documents'][0]['image_edit']['view']['page'] = 6
        measure('persist_view', lambda: store.save(workspace))
        measure('persist_load', store.load)
        workspace_bytes = store.path.stat().st_size
        asset_bytes = sum(p.stat().st_size for p in folder.glob('workspace.sqlite3.assets/*'))
        store.close()
        current, peak = tracemalloc.get_traced_memory()
    result = {'python': sys.version.split()[0], 'pymupdf': fitz.VersionBind, 'pages':80, 'pdf_bytes':path.stat().st_size, 'snapshot_bytes':len(encoded), 'workspace_bytes': workspace_bytes, 'asset_bytes': asset_bytes, 'python_peak_bytes':peak, 'memory_scope': 'tracemalloc Python allocations; excludes native MuPDF/Qt allocations', 'measurements':times}
    print(json.dumps(result, indent=2))
    editor.close(); restored.close()
    return result

if __name__ == '__main__':
    result = run(Path(sys.argv[1]))
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(json.dumps(result, indent=2), encoding='utf-8')
