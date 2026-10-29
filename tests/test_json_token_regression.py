from PySide6.QtTest import QTest

from test_ui import app, window  # noqa: F401
from test_scroll_sync import js, wait_for


def test_incremental_json_adds_new_token_colors(window):
    window.new_document('json')
    window.editor.setPlainText('{"a": 1}')
    window.set_mode('split')
    wait_for(lambda: window._preview_ready and window.selection_sync.enabled())
    source = '{"a": true, "b": null}'
    window.editor.selectAll()
    window.editor.insertPlainText(source)
    wait_for(lambda: window._preview_ready and js(window, 'document.body.textContent') == source)
    QTest.qWait(100)
    assert js(window, "nvSyntaxStyles.has('kc')")
    assert js(window, "document.getElementById('nv-syntax-colors').textContent.includes('::highlight(kc)')")
    wait_for(lambda: js(window, "CSS.highlights.has('kc')"))
    assert js(window, "getComputedStyle(document.querySelector('.highlight code'), '::highlight(kc)').color !== getComputedStyle(document.body).color")
