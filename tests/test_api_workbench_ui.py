import os
from pathlib import Path

os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')

import pytest
from PySide6.QtWidgets import QApplication, QLineEdit


@pytest.fixture(scope='session')
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def api_page(tmp_path, application):
    from notepadview.api_workbench.secrets import SecretVault
    from notepadview.api_workbench.store import ApiWorkbenchStore
    from notepadview.api_workbench.ui import ApiWorkbenchPage

    class MemoryKeyring:
        def __init__(self):
            self.values = {}

        def get_password(self, service, username):
            return self.values.get((service, username))

        def set_password(self, service, username, value):
            self.values[(service, username)] = value

    vault = SecretVault(keyring_backend=MemoryKeyring())
    store = ApiWorkbenchStore(Path(tmp_path) / 'api.sqlite3', vault=vault)
    page = ApiWorkbenchPage(store)
    yield page
    page.shutdown()
    application.processEvents()


def _test_archive(page):
    from notepadview.api_workbench.models import ApiCollection
    archive = next((item for item in page.store.list_collections()
                    if item.source != 'notepadview:interface-root'), None)
    if archive is None:
        archive = ApiCollection(id='test-module', name='Test module')
        page.store.save_collection(archive)
    return archive


@pytest.mark.parametrize('theme', ['nv', 'light', 'dark', 'soft'])
def test_parameter_editor_fits_cell_and_uses_theme_selection(api_page, application, theme):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor
    from notepadview.theme import THEMES, theme_stylesheet

    api_page.setStyleSheet(theme_stylesheet(theme))
    api_page.set_theme(theme)
    api_page.create_request()
    api_page.resize(1100, 800)
    api_page.show()
    table = api_page.request_editors['query'].table
    table.parentWidget().add_row({'key': 'search', 'value': 'value'})
    application.processEvents()
    item = table.item(0, 1)
    table.setCurrentItem(item)
    table.setFocus()
    application.processEvents()
    cell = table.visualItemRect(item)
    image = table.viewport().grab().toImage()
    selected_color = image.pixelColor(cell.right() - 20, cell.center().y())
    table.editItem(item)
    application.processEvents()
    editor = table.findChild(QLineEdit)
    assert editor is not None
    assert cell.contains(editor.geometry())
    assert editor.height() <= table.rowHeight(0)
    assert selected_color == QColor(THEMES[theme]['selection'])
    editor.setText('updated')
    from PySide6.QtTest import QTest
    QTest.keyClick(editor, Qt.Key_Return)
    application.processEvents()
    assert item.text() == 'updated'
    api_page.close()


@pytest.mark.parametrize('theme', ['nv', 'light', 'dark', 'soft'])
def test_interface_selection_keeps_theme_color_without_focus(api_page, application, theme):
    from PySide6.QtGui import QColor
    from notepadview.theme import THEMES, theme_stylesheet

    api_page.setStyleSheet(theme_stylesheet(theme))
    api_page.set_theme(theme)
    api_page.create_request()
    api_page.resize(1100, 800)
    api_page.show()
    tree = api_page.collection_tree
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    application.processEvents()
    rename_editor = tree.findChild(QLineEdit)
    assert rename_editor is not None
    QTest.keyClick(rename_editor, Qt.Key_Return)
    application.processEvents()
    tree.expandAll()
    tree.setFocus()
    application.processEvents()
    item = tree.currentItem()
    assert item is not None
    api_page.request_url_input.setFocus()
    application.processEvents()
    assert not tree.hasFocus()
    rect = tree.visualItemRect(item)
    image = tree.viewport().grab().toImage()
    assert image.pixelColor(rect.right() - 12, rect.center().y()) == QColor(THEMES[theme]['selection'])
    api_page.close()


def test_body_form_data_tabs_and_inline_upload(application, monkeypatch):
    import json
    from PySide6.QtWidgets import QFileDialog, QPushButton
    from notepadview.api_workbench.editors import BodyEditor

    body = BodyEditor()
    body.mode_tabs.setCurrentIndex(1)
    assert body.mode.currentText() == 'multipart'
    table = body.multipart.table
    assert table.horizontalHeader().visualIndex(3) == 2
    body.multipart.add_row({'key': 'attachment', 'type': 'file'})
    upload = table.cellWidget(0, 2)
    assert isinstance(upload, QPushButton)
    monkeypatch.setattr(QFileDialog, 'getOpenFileNames', lambda *args: (['sample.txt'], ''))
    upload.click()
    value = json.loads(body.toPlainText())[0]
    assert value['key'] == 'attachment'
    assert value['src'] == ['sample.txt']
    table.cellWidget(0, 3).setCurrentText('text')
    assert table.cellWidget(0, 2) is None
    body.mode.setCurrentText('form')
    assert body.mode_tabs.currentIndex() == 2
    body.deleteLater()


def test_disabled_request_area_click_explains_interface_requirement(api_page, application, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QToolTip

    messages = []
    monkeypatch.setattr(QToolTip, 'showText', lambda position, text, *args: messages.append(text))
    api_page.show()
    application.processEvents()
    assert api_page.request_guard.isVisible()
    QTest.mouseClick(api_page.request_guard, Qt.LeftButton)
    assert messages == ['请在接口下使用']
    assert not api_page.request_panel.isEnabled()
    api_page.create_request()
    application.processEvents()
    assert not api_page.request_guard.isVisible()
    assert api_page.request_panel.isEnabled()
    api_page._select_interface_root()
    application.processEvents()
    assert api_page.request_guard.isVisible()
    api_page.close()


def test_default_environments_are_persistent_and_not_duplicated(api_page):
    expected = ['开发环境', '测试环境', '生产环境']
    assert [item.name for item in api_page.environments] == expected
    development = api_page.environments[0]
    development.base_url = 'https://dev.example.test'
    api_page.store.save_environment(development)
    api_page._load_data()
    assert [item.name for item in api_page.environments] == expected
    assert api_page.environments[0].base_url == 'https://dev.example.test'
    assert len(api_page.store.list_environments()) == 3


def test_new_environment_uses_one_complete_form(api_page, monkeypatch):
    from PySide6.QtWidgets import QDialog, QInputDialog, QLineEdit, QPlainTextEdit

    def reject_sequential_dialog(*args, **kwargs):
        pytest.fail('环境配置应在同一表单展示')

    monkeypatch.setattr(QInputDialog, 'getText', reject_sequential_dialog)

    def complete_form(dialog):
        fields = dialog.findChildren(QLineEdit)
        editors = dialog.findChildren(QPlainTextEdit)
        assert len(fields) == 2
        from notepadview.api_workbench.editors import PairEditor
        assert len(editors) == 1
        headers = dialog.findChild(PairEditor)
        assert headers is not None
        assert headers.table.horizontalHeaderItem(1).text() == '参数名'
        fields[0].setText('自定义环境')
        fields[1].setText('https://custom.example.test')
        headers.add_button.click()
        headers.table.item(0, 1).setText('X-Client')
        headers.table.item(0, 2).setText('nv')
        editors[0].setPlainText('{"token":{"value":"example","secret":true}}')
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, 'exec', complete_form)
    api_page.create_environment()
    environment = api_page._selected_environment()
    assert environment.name == '自定义环境'
    assert environment.base_url == 'https://custom.example.test'
    assert environment.default_headers == {'X-Client': 'nv'}
    assert environment.variables['token']['secret'] is True


def test_edit_environment_preserves_id_and_cancel_does_not_save(api_page, monkeypatch):
    from PySide6.QtWidgets import QDialog, QLineEdit

    identifier = api_page._selected_environment().id

    def edit_form(dialog):
        fields = dialog.findChildren(QLineEdit)
        fields[1].setText('https://updated.example.test')
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, 'exec', edit_form)
    api_page.edit_environment()
    assert api_page._selected_environment().id == identifier
    assert api_page.store.get_environment(identifier).base_url == 'https://updated.example.test'

    monkeypatch.setattr(QDialog, 'exec', lambda dialog: QDialog.Rejected)
    api_page.create_environment()
    assert len(api_page.store.list_environments()) == 3


def test_api_workbench_page_exposes_request_and_response_controls(api_page):
    assert api_page.collection_tree is not None
    assert api_page.environment_selector is not None
    assert api_page.request_method_selector.currentText() == 'GET'
    assert api_page.response_tabs.count() >= 4


def test_request_items_hide_checkboxes_until_batch_delete_mode(api_page):
    from PySide6.QtCore import Qt
    from notepadview.api_workbench.models import RequestDefinition

    archive = _test_archive(api_page)
    archive.requests.append(RequestDefinition(id='request-checkbox', name='Request'))
    api_page.store.save_collection(archive)
    api_page._refresh_tree()
    item = api_page.collection_tree.topLevelItem(0).child(0).child(0)

    assert not item.flags() & Qt.ItemIsUserCheckable

    api_page._long_press_item = item
    api_page._enter_batch_delete_mode()

    assert item.flags() & Qt.ItemIsUserCheckable


def test_archive_toolbar_shows_add_and_remove_buttons(api_page):
    api_page.show()
    api_page.window().show()
    api_page.window().activateWindow()
    api_page.window().raise_()
    assert api_page.add_item_button.isVisible()
    assert api_page.remove_item_button.isVisible()
    for button in (api_page.add_item_button, api_page.remove_item_button):
        assert button.text() == ''
        assert not button.icon().isNull()
        assert not button.icon().pixmap(button.iconSize()).isNull()
        assert button.accessibleName() == button.toolTip()
        assert button.width() >= button.iconSize().width()
    assert api_page.add_item_button.icon().cacheKey() != api_page.remove_item_button.icon().cacheKey()


def test_empty_store_gets_one_persistent_default_archive(api_page):
    archives = api_page.store.list_collections()
    assert [archive.name for archive in archives] == ['接口管理']
    assert api_page.collection_tree.topLevelItemCount() == 1
    assert api_page.collection_tree.topLevelItem(0).text(0) == '接口管理'
    api_page._load_data()
    assert [archive.id for archive in api_page.store.list_collections()] == [archives[0].id]


def test_new_module_and_root_request_are_inline_without_dialogs(api_page, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: pytest.fail('Unexpected dialog'))
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *a, **k: pytest.fail('Unexpected dialog'))
    api_page.create_request()
    root_request = api_page.current_request
    assert root_request.name == '未命名接口'
    assert api_page.collection_tree.currentItem().parent() is api_page.collection_tree.topLevelItem(0)
    api_page.show()
    api_page.create_archive()
    module = api_page.collection_tree.currentItem()
    assert module.text(0) == '未命名模块'
    api_page.create_request()
    assert api_page.collection_tree.currentItem().parent().text(0) == '未命名模块'
    assert api_page.current_collection.name == '未命名模块'
    assert len(api_page.store.list_collections()) == 2


def test_root_cannot_be_deleted_and_can_receive_new_request(api_page):
    api_page.create_archive()
    api_page._select_tree_item_by_id('collection', api_page._root_collection_id)
    api_page._select_tree_item(api_page.collection_tree.currentItem())
    assert not api_page.remove_item_button.isEnabled()
    api_page.delete_tree_item()
    assert api_page.store.get_collection(api_page._root_collection_id) is not None
    api_page.create_request()
    assert api_page.current_collection.id == api_page._root_collection_id
    assert api_page.collection_tree.currentItem().parent() is api_page.collection_tree.topLevelItem(0)


def test_legacy_empty_defaults_removed_but_data_and_variables_retained(api_page):
    from notepadview.api_workbench.models import ApiCollection, RequestDefinition
    api_page.store.save_collection(ApiCollection(id='empty', name='我的请求'))
    api_page.store.save_collection(ApiCollection(id='kept', name='新归档', requests=[
        RequestDefinition(id='legacy', name='Legacy', url='/legacy')]))
    api_page.store.save_collection(ApiCollection(id='variables', name='我的请求',
        variables={'host': {'value': 'example', 'secret': False}}))
    root_id = api_page._root_collection_id
    api_page._load_data()
    api_page._load_data()
    assert api_page._root_collection_id == root_id
    assert api_page.store.get_collection('empty') is None
    assert api_page.store.get_collection('kept').requests[0].url == '/legacy'
    assert api_page.store.get_collection('variables').variables['host']['value'] == 'example'


def test_batch_button_clears_all_nested_checks(api_page):
    from PySide6.QtCore import Qt
    api_page.create_archive()
    api_page.create_request()
    api_page.batch_select_button.click()
    assert api_page.batch_select_button.isChecked()
    items = list(api_page._tree_items())
    for item in items:
        if item.flags() & Qt.ItemIsUserCheckable:
            item.setCheckState(0, Qt.Checked)
    api_page.batch_select_button.click()
    assert not api_page._batch_delete_mode
    assert all(item.data(0, Qt.CheckStateRole) is None for item in items)


def test_existing_collection_prevents_default_archive_creation(api_page):
    from notepadview.api_workbench.models import ApiCollection

    default_archive = _test_archive(api_page)
    api_page.store.delete_collection(default_archive.id)
    api_page.store.save_collection(ApiCollection(id='existing', name='Existing'))

    api_page._load_data()

    assert {archive.name for archive in api_page.store.list_collections()} == {'接口管理', 'Existing'}


def test_openapi_operations_are_direct_archive_children(api_page):
    from PySide6.QtCore import Qt
    from notepadview.api_workbench.models import ApiCollection, ApiOperation, RequestDefinition

    collection = ApiCollection(
        id='tagged-api',
        name='Tagged API',
        operations=[
            ApiOperation(
                id='tagged-operation', method='GET', path='/items',
                summary='List items', tags=('Items',)),
            ApiOperation(
                id='untagged-operation', method='POST', path='/items'),
        ],
        requests=[RequestDefinition(id='saved-request', name='Saved request')],
    )
    api_page.store.save_collection(collection)
    api_page._refresh_tree()

    archive = next(
        item for item in api_page._tree_items() if item.text(0) == 'Tagged API'
    )
    assert archive.childCount() == 3
    assert {
        archive.child(index).data(0, Qt.UserRole)[0]
        for index in range(archive.childCount())
    } == {'operation', 'request'}
    assert all(archive.child(index).childCount() == 0 for index in range(archive.childCount()))


def test_api_workbench_theme_can_be_changed_without_rebuilding_page(api_page):
    api_page.set_theme('dark')
    assert api_page.theme == 'dark'
    api_page.set_theme('soft')
    assert api_page.theme == 'soft'


def test_postman_collection_exchange_controls_are_removed(api_page):
    assert not hasattr(api_page, 'import_postman_button')
    assert not hasattr(api_page, 'export_postman_button')


def test_plus_has_explicit_archive_and_request_actions(api_page):
    assert [action.text() for action in api_page.add_item_menu.actions()] == ['新增模块', '新增接口']


def test_double_click_renames_archive_and_request(api_page, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: pytest.fail('Unexpected dialog'))
    api_page.create_archive()
    module = api_page.collection_tree.currentItem()
    editor = api_page.collection_tree.findChildren(QLineEdit)[-1]
    editor.setText('Renamed module')
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt
    QTest.keyClick(editor, Qt.Key_Return)
    QApplication.processEvents()
    api_page.create_request()
    item = api_page.collection_tree.currentItem()
    editor = api_page.collection_tree.findChildren(QLineEdit)[-1]
    editor.setText('After')
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt
    QTest.keyClick(editor, Qt.Key_Return)
    QApplication.processEvents()
    api_page.request_url_input.setText('/before')
    api_page.save_current_request()
    saved = api_page.store.get_collection(api_page.current_collection.id)
    assert saved.name == 'Renamed module'
    assert saved.requests[0].name == 'After'
    assert saved.requests[0].url == '/before'
    api_page._load_data()
    assert api_page.store.get_collection(saved.id).requests[0].name == 'After'


def test_double_click_renames_openapi_operation(api_page):
    from notepadview.api_workbench.models import ApiCollection, ApiOperation
    collection = ApiCollection(id='operations', name='Operations', operations=[
        ApiOperation(id='operation', method='GET', path='/items', summary='Before')])
    api_page.store.save_collection(collection)
    api_page._refresh_tree()
    item = api_page._select_tree_item_by_id('operation', 'operation')
    api_page.show()
    api_page.collection_tree.itemDoubleClicked.emit(item, 0)
    editor = api_page.collection_tree.findChildren(QLineEdit)[-1]
    editor.setText('After')
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt
    QTest.keyClick(editor, Qt.Key_Return)
    QApplication.processEvents()
    assert api_page.store.get_collection(collection.id).operations[0].summary == 'After'
    assert item.text(0) == 'GET  After'


def test_escape_rename_restores_display_and_edit_flags(api_page):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    api_page.create_request()
    item = api_page.collection_tree.currentItem()
    editor = api_page.collection_tree.findChildren(QLineEdit)[-1]
    editor.setText('Cancelled name')
    QTest.keyClick(editor, Qt.Key_Escape)
    QApplication.processEvents()
    assert item.text(0) == 'GET  未命名接口'
    assert not item.flags() & Qt.ItemIsEditable
    assert api_page.store.get_collection(api_page._root_collection_id).requests[0].name == '未命名接口'


def test_cancelled_rename_keeps_archive_name(api_page):
    api_page.create_archive()
    item = api_page.collection_tree.currentItem()
    editor = api_page.collection_tree.findChildren(QLineEdit)[-1]
    editor.setText('')
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt
    QTest.keyClick(editor, Qt.Key_Return)
    QApplication.processEvents()
    assert api_page.store.get_collection(item.data(0, 256)[1]).name == '未命名模块'


def test_minus_deletes_selected_saved_request(api_page, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Yes)
    from PySide6.QtCore import Qt
    from notepadview.api_workbench.models import RequestDefinition

    archive = _test_archive(api_page)
    archive.requests.append(RequestDefinition(id='delete-request', name='Delete me'))
    api_page.store.save_collection(archive)
    api_page._refresh_tree()
    root = api_page.collection_tree.topLevelItem(0).child(0)
    request_item = next(
        root.child(index) for index in range(root.childCount())
        if root.child(index).data(0, Qt.UserRole)[0] == 'request'
    )
    api_page.collection_tree.setCurrentItem(request_item)

    api_page.remove_item_button.click()

    assert api_page.store.get_collection(archive.id).requests == []


def test_minus_deletes_selected_openapi_operation(api_page, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Yes)
    from PySide6.QtCore import Qt
    from notepadview.api_workbench.models import ApiCollection, ApiOperation

    collection = ApiCollection(
        id='delete-operation-archive', name='Operation archive',
        operations=[ApiOperation(id='delete-operation', method='GET', path='/items')])
    api_page.store.save_collection(collection)
    api_page._refresh_tree()
    root = next(
        item
        for item in api_page._tree_items()
        if item.data(0, Qt.UserRole)[1]
        == collection.id
    )
    api_page.collection_tree.setCurrentItem(root.child(0))

    api_page.remove_item_button.click()

    assert api_page.store.get_collection(collection.id).operations == []


def test_minus_deletes_archive_only_after_confirmation(api_page, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMessageBox
    from notepadview.api_workbench.models import ApiCollection, RequestDefinition

    archive = ApiCollection(
        id='delete-archive', name='Delete archive',
        requests=[RequestDefinition(id='child-request', name='Child')])
    api_page.store.save_collection(archive)
    api_page._refresh_tree()
    root = next(
        item
        for item in api_page._tree_items()
        if item.data(0, Qt.UserRole)[1]
        == archive.id
    )
    api_page.collection_tree.setCurrentItem(root)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.Yes)

    api_page.remove_item_button.click()

    assert api_page.store.get_collection(archive.id) is None


def test_cancelled_archive_delete_keeps_archive(api_page, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMessageBox
    from notepadview.api_workbench.models import ApiCollection

    archive = ApiCollection(id='keep-archive', name='Keep archive')
    api_page.store.save_collection(archive)
    api_page._refresh_tree()
    root = next(
        item
        for item in api_page._tree_items()
        if item.data(0, Qt.UserRole)[1]
        == archive.id
    )
    api_page.collection_tree.setCurrentItem(root)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.No)

    api_page.remove_item_button.click()

    assert api_page.store.get_collection(archive.id) is not None


def test_long_press_enables_request_checkboxes_and_batch_deletes(api_page, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Yes)
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from notepadview.api_workbench.models import RequestDefinition

    archive = _test_archive(api_page)
    archive.requests.extend([
        RequestDefinition(id='batch-one', name='Batch one'),
        RequestDefinition(id='batch-two', name='Batch two'),
        RequestDefinition(id='keep-one', name='Keep one'),
    ])
    api_page.store.save_collection(archive)
    api_page._refresh_tree()
    root = api_page.collection_tree.topLevelItem(0).child(0)
    first = root.child(0)
    second = root.child(1)
    api_page.show()
    api_page.collection_tree.expandAll()
    point = api_page.collection_tree.visualItemRect(first).center()
    QTest.mousePress(api_page.collection_tree.viewport(), Qt.LeftButton, pos=point)
    QTest.qWait(650)
    assert api_page._batch_delete_mode
    assert first.flags() & Qt.ItemIsUserCheckable
    QTest.mouseRelease(api_page.collection_tree.viewport(), Qt.LeftButton, pos=point)
    first.setCheckState(0, Qt.Checked)
    QTest.mousePress(api_page.collection_tree.viewport(), Qt.LeftButton, pos=point)
    QTest.qWait(650)
    QTest.mouseRelease(api_page.collection_tree.viewport(), Qt.LeftButton, pos=point)
    assert first.checkState(0) == Qt.Checked
    second.setCheckState(0, Qt.Checked)

    api_page.remove_item_button.click()

    assert [request.id for request in api_page.store.get_collection(
        archive.id).requests] == ['keep-one']
    assert not api_page._batch_delete_mode


def test_escape_cancels_batch_delete_without_removing_requests(api_page):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from notepadview.api_workbench.models import RequestDefinition

    archive = _test_archive(api_page)
    archive.requests.append(RequestDefinition(id='batch-keep', name='Keep'))
    api_page.store.save_collection(archive)
    api_page._refresh_tree()
    root = api_page.collection_tree.topLevelItem(0).child(0)
    item = root.child(0)
    api_page.show()
    api_page.collection_tree.expandAll()
    point = api_page.collection_tree.visualItemRect(item).center()
    QTest.mousePress(api_page.collection_tree.viewport(), Qt.LeftButton, pos=point)
    QTest.qWait(650)
    QTest.mouseRelease(api_page.collection_tree.viewport(), Qt.LeftButton, pos=point)
    QTest.keyClick(api_page.collection_tree.viewport(), Qt.Key_Escape)

    assert not api_page._batch_delete_mode
    assert [request.id for request in api_page.store.get_collection(
        archive.id).requests] == ['batch-keep']


def test_request_editors_are_real_controls_in_config_tabs(api_page):
    from PySide6.QtWidgets import QPlainTextEdit, QTableWidget

    api_page.create_request()

    assert [api_page.request_tabs.tabText(i) for i in range(
        api_page.request_tabs.count())] == [
        'Params', 'Body', 'Headers', 'Cookies', 'Auth', '前置操作', '后置操作', '设置']
    for key in ('query', 'headers', 'cookies'):
        assert api_page.request_editors[key].findChild(QTableWidget) is not None
        assert not isinstance(api_page.request_editors[key], QPlainTextEdit)
    query = api_page.request_editors['query']
    assert query.table.horizontalHeaderItem(1).text() == '参数名'
    assert query.table.horizontalHeaderItem(2).text() == '参数值'
    assert query.table.verticalHeader().isHidden()
    assert not query.table.showGrid()
    assert query.add_button.text() == '添加参数'
    assert query.remove_button.isHidden()
    query.add_button.click()
    query.table.item(0, 1).setText('search')
    query.table.item(0, 2).setText('example')
    assert 'search' in query.toPlainText()
    assert 'example' in query.toPlainText()
    assert api_page.response_tabs.count() == 5


def test_response_uses_compact_readonly_tables(api_page):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QAbstractItemView
    from notepadview.api_workbench.editors import PairEditor

    assert 'border-bottom:2px solid #8b5cf6' in api_page.response_tabs.styleSheet()
    for key in ('headers', 'cookies'):
        editor = api_page.response_editors[key]
        assert isinstance(editor, PairEditor)
        editor.setPlainText('{"example":"value"}')
        assert editor.table.item(0, 1).text() == 'example'
        assert editor.table.item(0, 2).text() == 'value'
        assert editor.table.editTriggers() == QAbstractItemView.NoEditTriggers
        assert editor.table.isColumnHidden(0)
        assert editor.add_button.isHidden()
        assert not editor.table.item(0, 0).flags() & Qt.ItemIsUserCheckable


def test_save_button_follows_send_and_does_not_start_request(api_page, monkeypatch):
    import pytest
    from notepadview.api_workbench.models import RequestDefinition

    archive = _test_archive(api_page)
    archive.requests.append(RequestDefinition(id='save-request', name='Save request'))
    api_page.store.save_collection(archive)
    api_page._refresh_tree()
    selected = api_page._select_tree_item_by_id('request', 'save-request')
    api_page._select_tree_item(selected)
    api_page.request_url_input.setText('https://api.example.test/saved')
    api_page.request_method_selector.setCurrentText('POST')
    api_page.request_editors['body'].setPlainText('{"saved":true}')
    api_page.body_mode_selector.setCurrentText('raw')
    api_page.response_status.setText('Keep response unchanged')
    monkeypatch.setattr(
        api_page.thread_pool, 'start',
        lambda *_args, **_kwargs: pytest.fail('Save must not execute a request'))

    api_page.save_button.click()

    saved = api_page.store.get_collection(archive.id).requests[0]
    assert saved.url == 'https://api.example.test/saved'
    assert saved.method == 'POST'
    assert saved.body == '{"saved":true}'
    assert api_page.response_status.text() == 'Keep response unchanged'
    assert api_page.request_row_layout.indexOf(api_page.save_button) == (
        api_page.request_row_layout.indexOf(api_page.send_button) + 1)


def test_send_does_not_persist_before_or_after_worker(api_page, monkeypatch):
    from notepadview.api_workbench.models import RequestDefinition

    archive = _test_archive(api_page)
    archive.requests.append(RequestDefinition(id='send-request', name='Send request'))
    api_page.store.save_collection(archive)
    api_page._refresh_tree()
    selected = api_page._select_tree_item_by_id('request', 'send-request')
    api_page._select_tree_item(selected)
    api_page.request_url_input.setText('https://api.example.test/before-send')
    workers = []
    monkeypatch.setattr(api_page.thread_pool, 'start', workers.append)
    api_page.send_current_request()
    assert api_page.store.get_collection(archive.id).requests[0].url == ''
    from notepadview.api_workbench.ui import _ExecutionOutput
    from notepadview.api_workbench.runner import RequestResult, ResponseSnapshot
    api_page._request_finished(_ExecutionOutput(
        RequestResult(response=ResponseSnapshot(200, 'OK', 1, 2, body='{}')),
        workers[0].request, workers[0].environment))
    assert api_page.store.get_collection(archive.id).requests[0].url == ''
    assert len(api_page.store.list_history()) == 1


def test_environment_switch_keeps_unsaved_editor(api_page):
    api_page.request_url_input.setText('/draft')
    api_page.environment_selector.setCurrentIndex(1)
    assert api_page.request_url_input.text() == '/draft'
    assert api_page.is_dirty


def test_batch_done_clears_check_state_role(api_page):
    from PySide6.QtCore import Qt
    from notepadview.api_workbench.models import RequestDefinition
    archive = _test_archive(api_page)
    archive.requests.append(RequestDefinition(id='check-role', name='Check'))
    api_page.store.save_collection(archive)
    api_page._refresh_tree()
    item = api_page.collection_tree.topLevelItem(0).child(0).child(0)
    assert item.data(0, Qt.CheckStateRole) is None
    api_page._long_press_item = item
    api_page._enter_batch_delete_mode()
    assert api_page.batch_select_button.isChecked()
    api_page.batch_select_button.click()
    assert item.data(0, Qt.CheckStateRole) is None


def test_module_disables_request_panel_and_blocks_actions(api_page, monkeypatch):
    from notepadview.api_workbench.models import RequestDefinition

    archive = _test_archive(api_page)
    archive.requests.append(RequestDefinition(id='enabled-request', name='Request'))
    api_page.store.save_collection(archive)
    api_page._refresh_tree()
    api_page._select_tree_item(api_page._select_tree_item_by_id('request', 'enabled-request'))
    assert api_page.request_panel.isEnabled()
    api_page._select_tree_item(api_page._select_tree_item_by_id('collection', archive.id))
    for widget in (api_page.request_panel, api_page.send_button, api_page.save_button,
                   api_page.request_url_input, api_page.request_tabs, api_page.response_tabs):
        assert not widget.isEnabled()
    monkeypatch.setattr(api_page, '_request_from_editor',
                        lambda: pytest.fail('模块节点不应执行请求操作'))
    api_page.send_current_request()
    api_page.save_current_request()
    api_page._select_tree_item(api_page._select_tree_item_by_id('request', 'enabled-request'))
    assert api_page.request_panel.isEnabled()
    api_page._select_interface_root()
    assert not api_page.request_panel.isEnabled()


def test_switch_request_discards_draft_without_prompt(api_page, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from notepadview.api_workbench.models import RequestDefinition
    archive = _test_archive(api_page)
    archive.requests.append(RequestDefinition(id='other', name='Other', url='/other'))
    api_page.store.save_collection(archive)
    api_page._refresh_tree()
    api_page.request_url_input.setText('/draft')
    def unexpected_prompt(*args, **kwargs):
        pytest.fail('请求切换不应弹出未保存提示')
    monkeypatch.setattr(QMessageBox, 'warning', unexpected_prompt)
    api_page._select_tree_item(api_page.collection_tree.topLevelItem(0).child(0).child(0))
    assert api_page.request_url_input.text() == '/other'


def test_history_items_can_be_opened_for_review_and_replay(api_page):
    from notepadview.api_workbench.models import RequestHistory

    api_page.store.save_history(RequestHistory(
        id='history-1',
        completed_at='2026-10-25T12:00:00+00:00',
        request={'id': 'history-request', 'name': 'Get health', 'method': 'GET',
                 'url': 'https://api.example.test/health', 'query': [],
                 'headers': {'Authorization': 'Bearer history-token'},
                 'auth': {'type': 'bearer', 'token': 'history-token'},
                 'cookies': {'sid': 'history-cookie'},
                 'body_mode': 'none', 'body': '', 'pre_script': '',
                 'post_script': '', 'variables': {}, 'folder': ''},
        response={'status_code': 200, 'status': 'OK',
                  'headers': {'Set-Cookie': 'sid=history-cookie'},
                  'cookies': {'sid': 'history-cookie'},
                  'body': '{"ok":true}', 'elapsed_ms': 4, 'size_bytes': 11},
    ))
    api_page._refresh_tree()
    history_root = next(
        api_page.collection_tree.topLevelItem(index)
        for index in range(api_page.collection_tree.topLevelItemCount())
        if api_page.collection_tree.topLevelItem(index).text(0) == '历史记录'
    )

    api_page._select_tree_item(history_root.child(0))

    assert api_page.request_url_input.text() == 'https://api.example.test/health'
    assert 'history-token' not in api_page.request_editors['auth'].toPlainText()
    assert 'history-cookie' not in api_page.request_editors['cookies'].toPlainText()
    assert api_page.response_status.text().startswith('200 OK')
    assert api_page.response_editors['raw'].toPlainText() == '{"ok":true}'
    replay_request = api_page._request_from_editor()
    assert replay_request.auth['token'] == 'history-token'
    assert replay_request.cookies == {'sid': 'history-cookie'}
    assert 'history-cookie' not in api_page.response_editors['headers'].toPlainText()
    assert 'history-cookie' not in api_page.response_editors['cookies'].toPlainText()


def test_switching_workspaces_preserves_unsaved_document(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    application = QApplication.instance() or QApplication([])
    from notepadview.window import MainWindow

    class MemoryKeyring:
        def __init__(self):
            self.values = {}

        def get_password(self, service, username):
            return self.values.get((service, username))

        def set_password(self, service, username, value):
            self.values[(service, username)] = value

    from notepadview.api_workbench.secrets import SecretVault
    monkeypatch.setattr(
        'notepadview.api_workbench.secrets.SecretVault',
        lambda: SecretVault(keyring_backend=MemoryKeyring()),
    )
    settings = QSettings(str(tmp_path / 'window.ini'), QSettings.IniFormat)
    window = MainWindow(settings=settings)
    try:
        assert window.theme == 'nv'
        assert window.theme_actions['nv'].text() == 'NV 主题'
        assert not window.api_workbench_action.isVisible()
        assert not window.api_workbench_action.isEnabled()
        assert window.set_main_workspace('api') is False
        assert window.api_workbench is None
        assert window.main_workspace.currentWidget() is window.document_workspace
        window.api_workbench_action.setVisible(True)
        window.api_workbench_action.setEnabled(True)
        window.set_main_workspace('api')
        assert window.api_workbench.theme == 'nv'
        window.set_main_workspace('documents')
        window.new_document('txt')
        window.editor.setPlainText('unsaved text')
        window.set_main_workspace('api')
        assert window.main_workspace.currentWidget() is window.api_workbench
        window.set_main_workspace('documents')
        assert window.editor.toPlainText() == 'unsaved text'
        assert window.current_session is not None
    finally:
        for session in window.sessions:
            session.document.setModified(False)
        window.editor.document().setModified(False)
        window.close()
        application.processEvents()


def test_data_controls_prepare_real_protocol(api_page, tmp_path):
    from PySide6.QtWidgets import QTableWidgetItem
    from notepadview.api_workbench.requests import prepare_request
    api_page.request_url_input.setText('https://example.test/upload')
    query = api_page.request_editors['query']
    query.setPlainText('[{"key":"tag","value":"a"},{"key":"tag","value":"b"},{"key":"skip","value":"x","disabled":true}]')
    query.table.item(0, 2).setText('edited')
    auth = api_page.request_editors['auth']
    auth.mode.setCurrentText('api_key')
    auth.fields['key'].setText('access')
    auth.fields['value'].setText('secret')
    auth.location.setCurrentText('query')
    body = api_page.request_editors['body']
    body.mode.setCurrentText('form')
    body.form.setPlainText('[{"key":"field","value":"value"}]')
    prepared = prepare_request(api_page._request_from_editor(), api_page._selected_environment())
    assert prepared.params == [('tag', 'edited'), ('tag', 'b'), ('access', 'secret')]
    assert prepared.data == [('field', 'value')]
    upload = tmp_path / 'upload.txt'
    upload.write_text('binary content', encoding='utf-8')
    body.mode.setCurrentText('multipart')
    body.multipart.add_row({'key': 'attachment', 'type': 'file', 'src': str(upload)})
    prepared = prepare_request(api_page._request_from_editor(), api_page._selected_environment())
    assert prepared.files == [('attachment', str(upload))]
    body.mode.setCurrentText('binary')
    body.path.setText(str(upload))
    assert prepare_request(api_page._request_from_editor(), api_page._selected_environment()).content == b'binary content'


def test_worker_passes_selected_environment_to_preparation(api_page, monkeypatch):
    from notepadview.api_workbench.ui import _ApiExecutionWorker
    from notepadview.api_workbench.models import RequestDefinition
    from notepadview.api_workbench.runner import CancelToken, RequestResult, ResponseSnapshot
    monkeypatch.setattr('notepadview.api_workbench.ui.send_request',
                        lambda request, **kw: RequestResult(response=ResponseSnapshot(200, 'OK', 1, 0)))
    environment = api_page._selected_environment()
    environment.base_url = 'https://example.test'
    worker = _ApiExecutionWorker(RequestDefinition(id='draft', name='Draft', url='/health'),
                                 environment, {}, {}, CancelToken())
    outputs = []
    worker.signals.finished.connect(outputs.append)
    worker.run()
    assert not outputs[0].error
    assert outputs[0].result.response.status_code == 200


def test_body_mode_switch_preserves_edits(api_page):
    body = api_page.request_editors['body']
    body.mode.setCurrentText('raw')
    body.setPlainText('original')
    body.raw.setPlainText('edited')
    body.mode.setCurrentText('none')
    body.mode.setCurrentText('raw')
    assert body.toPlainText() == 'edited'


def test_history_save_keeps_original_secret_for_next_send(api_page):
    from notepadview.api_workbench.models import RequestDefinition
    api_page.current_request = RequestDefinition(id='history-save', name='History', cookies={'sid': 'original'})
    api_page._is_history_selection = True
    api_page._load_selected_request()
    api_page.save_current_request()
    assert api_page._request_from_editor().cookies == {'sid': 'original'}


def test_multipart_add_emits_only_complete_rows(api_page):
    editor = api_page.request_editors['body'].multipart
    errors = []
    def read():
        try:
            editor.toPlainText()
        except AttributeError as error:
            errors.append(error)
    editor.textChanged.connect(read)
    editor.add_row({'key': 'file', 'type': 'file', 'src': 'example.txt'})
    assert errors == []


def test_auth_invalid_input_can_be_replaced_by_visible_form(api_page):
    import json
    auth = api_page.request_editors['auth']
    auth.setPlainText('{bad')
    auth.mode.setCurrentText('bearer')
    auth.fields['token'].setText('replacement')
    assert json.loads(auth.toPlainText())['token'] == 'replacement'


def test_search_matches_request_url_and_survives_refresh(api_page):
    from notepadview.api_workbench.models import RequestDefinition
    archive = _test_archive(api_page)
    archive.requests.extend([RequestDefinition(id='search-a', name='A', url='/needle'),
                             RequestDefinition(id='search-b', name='B', url='/other')])
    api_page.store.save_collection(archive)
    api_page._refresh_tree()
    api_page.search_input.setText('needle')
    root = api_page.collection_tree.topLevelItem(0).child(0)
    assert not root.child(0).isHidden()
    assert root.child(1).isHidden()
    api_page._refresh_tree()
    assert api_page.collection_tree.topLevelItem(0).child(0).child(1).isHidden()


def test_switch_request_does_not_automatically_save_draft(api_page, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from notepadview.api_workbench.models import RequestDefinition
    archive = _test_archive(api_page)
    archive.requests.append(RequestDefinition(id='destination', name='Destination', url='/destination'))
    api_page.store.save_collection(archive)
    api_page._refresh_tree()
    api_page.request_url_input.setText('/saved-draft')
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a, **k: QMessageBox.Save)
    api_page._select_tree_item(api_page.collection_tree.topLevelItem(0).child(0).child(0))
    assert api_page.request_url_input.text() == '/destination'
    assert '/saved-draft' not in [r.url for r in api_page.store.get_collection(api_page._root_collection_id).requests]


def test_environment_switch_keeps_loaded_request_edits(api_page):
    from notepadview.api_workbench.models import RequestDefinition
    api_page.current_request = RequestDefinition(id='environment-draft', name='Draft', url='/original')
    api_page._load_selected_request()
    api_page.request_url_input.setText('/edited')
    api_page.environment_selector.setCurrentIndex(1)
    assert api_page.request_url_input.text() == '/edited'
    assert api_page.is_dirty


def test_history_replays_script_assertions(api_page):
    from notepadview.api_workbench.models import RequestHistory, RequestDefinition
    history = RequestHistory(id='script-history', completed_at='2026-10-26T00:00:00+00:00',
                             request=RequestDefinition(id='script', name='Script').to_dict(),
                             response={'status_code': 200, 'body': '{}', 'tests': ['PASS status']})
    api_page.response_editors['tests'].setPlainText('stale tests')
    api_page._show_history_response(history)
    assert api_page.response_editors['tests'].toPlainText() == 'PASS status'


def test_interface_root_row_keeps_icons_alive_across_refreshes(api_page, application):
    from PySide6.QtCore import QCoreApplication, QEvent, Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QWidget
    api_page.show()
    buttons = (api_page.add_item_button, api_page.batch_select_button,
               api_page.remove_item_button)
    for _ in range(4):
        api_page._refresh_tree()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        application.processEvents()
        root = api_page.collection_tree.topLevelItem(0)
        assert root.text(0) == '接口管理'
        assert root.data(0, Qt.UserRole) == ('collection', api_page._root_collection_id)
        row = api_page.collection_tree.itemWidget(root, 0)
        assert isinstance(row, QWidget)
        assert all(button.parentWidget() is row for button in buttons)
        assert [row.layout().indexOf(button) for button in buttons] == [2, 3, 4]
        for button in buttons:
            assert button.isVisible()
            assert button.text() == ''
            assert button.accessibleName() == button.toolTip()
            assert button.width() >= button.iconSize().width() + 8
            assert button.height() >= button.iconSize().height() + 8
            assert not button.icon().pixmap(button.iconSize()).isNull()
    api_page.batch_select_button.click()
    assert api_page.batch_select_button.isChecked()
    assert api_page.batch_select_button.text() == ''
    icon = api_page.batch_select_button.icon()
    assert icon.pixmap(24, 24, QIcon.Normal, QIcon.On).toImage() != icon.pixmap(24, 24).toImage()
    old_icon = icon.cacheKey()
    api_page.set_theme('dark')
    assert api_page.batch_select_button.icon().cacheKey() != old_icon
    api_page.batch_select_button.click()
    assert not api_page.batch_select_button.isChecked()


def test_real_root_is_protected_and_third_level_batch_delete_works(api_page, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMessageBox
    from notepadview.api_workbench.models import ApiCollection, ApiOperation, RequestDefinition
    root_collection = api_page.store.get_collection(api_page._root_collection_id)
    root_collection.requests.append(RequestDefinition(id='direct', name='Direct', url='/direct'))
    api_page.store.save_collection(root_collection)
    module = ApiCollection(id='nested', name='Nested', requests=[
        RequestDefinition(id='nested-request', name='Nested request', url='/needle')],
        operations=[ApiOperation(id='nested-operation', method='GET', path='/needle-operation')])
    api_page.store.save_collection(module)
    api_page._refresh_tree()
    root = api_page.collection_tree.topLevelItem(0)
    api_page.collection_tree.setCurrentItem(root)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: pytest.fail('Root deletion must not prompt'))
    api_page.delete_tree_item()
    api_page._rename_tree_item(root)
    assert not root.flags() & Qt.ItemIsEditable
    assert not api_page.remove_item_button.isEnabled()
    assert api_page.store.get_collection(root_collection.id).name == '接口管理'
    nested = api_page._select_tree_item_by_id('request', 'nested-request')
    assert nested.parent().parent() is root
    api_page._select_tree_item(nested)
    assert api_page.current_request.id == 'nested-request'
    api_page.search_input.setText('needle')
    assert not root.isHidden() and not nested.parent().isHidden() and not nested.isHidden()
    direct = api_page._select_tree_item_by_id('request', 'direct')
    assert direct.isHidden()
    api_page.batch_select_button.click()
    assert not root.flags() & Qt.ItemIsUserCheckable
    for item in api_page._tree_items():
        if item.data(0, Qt.UserRole)[0] in ('request', 'operation'):
            assert item.flags() & Qt.ItemIsUserCheckable
            item.setCheckState(0, Qt.Checked)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Yes)
    api_page.remove_item_button.click()
    assert api_page.store.get_collection(root_collection.id).requests == []
    assert api_page.store.get_collection(module.id).requests == []
    assert api_page.store.get_collection(module.id).operations == []
    assert not api_page._batch_delete_mode
