from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QContextMenuEvent, QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QInputDialog, QLineEdit, QMenu, QWidget

from notepadview import file_tree
from notepadview.file_tree import FileTree


class TreeOwner(QWidget):
    def __init__(self):
        super().__init__()
        self.default_category_name = '默认分类'
        self.categories = [{'id': 'work', 'name': '工作', 'expanded': True}]
        self.sessions = [
            SimpleNamespace(id='first', name='first.txt', category_id='',
                            path=Path('first.txt'), kind='text', document_format='txt', document=QTextDocument()),
            SimpleNamespace(id='second', name='second.txt', category_id='',
                            path=Path('second.txt'), kind='text', document_format='txt', document=QTextDocument()),
            SimpleNamespace(id='draft', name='draft.txt', category_id='work',
                            path=None, kind='text', document_format='txt', document=QTextDocument()),
        ]
        self.current_session = self.sessions[0]
        self.activations = []
        self.renames = []
        self.document_renames = []
        self.removals = []
        self.moves = []
        self.opened_folders = []
        self.new_documents = []
        self.new_categories = []
        self.deleted_categories = []
        self.tree = FileTree(self)
        self.tree.resize(460, 360)
        self.resize(460, 360)
        self.refresh_file_list()

    def activate_file_item(self, item):
        session = item.data(0, Qt.UserRole)
        if session is not None:
            self.activations.append(session)
            self.current_session = session

    def refresh_file_list(self):
        self.tree.refresh(self.sessions, self.current_session)

    def rename_category(self, category_id, name):
        self.renames.append((category_id, name))
        name = name.strip()
        names = {category['name'] for category in self.categories if category['id'] != category_id}
        if category_id:
            names.add(self.default_category_name)
        if not name or name in names:
            return False
        if category_id:
            next(category for category in self.categories if category['id'] == category_id)['name'] = name
        else:
            self.default_category_name = name
        self.refresh_file_list()
        return True

    def request_rename_document(self, session):
        self.document_renames.append(session)

    def remove_sessions(self, sessions):
        self.removals.append([session.id for session in sessions])

    def move_sessions_to_category(self, sessions, category_id):
        self.moves.append(([session.id for session in sessions], category_id))

    def open_document_folder(self, session):
        self.opened_folders.append(session)
        return session.path is not None

    def choose_new_document(self, *, category_id):
        self.new_documents.append(category_id)

    def create_category(self, name):
        self.new_categories.append(name)

    def delete_category(self, category_id):
        self.deleted_categories.append(category_id)

    def schedule_workspace_save(self):
        pass


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def owner(app, monkeypatch):
    mock_menu_exec(monkeypatch, lambda *_args: None)
    widget = TreeOwner()
    widget.show()
    app.processEvents()
    widget.activations.clear()
    yield widget
    widget.close()
    widget.deleteLater()
    app.processEvents()


def test_middle_click_removes_only_pointed_file_without_activation(owner):
    tree = owner.tree
    tree.setSelectionMode(tree.SelectionMode.MultiSelection)
    for session in owner.sessions[:2]:
        tree.item_for_session(session).setSelected(True)
    owner.activations.clear()
    target = tree.item_for_session(owner.sessions[1])
    QTest.mouseClick(tree.viewport(), Qt.MiddleButton, pos=tree.visualItemRect(target).center())
    assert owner.removals == [['second']]
    assert owner.activations == []


def test_middle_click_category_and_empty_space_does_not_remove(owner):
    tree = owner.tree
    category = tree.topLevelItem(0)
    QTest.mouseClick(tree.viewport(), Qt.MiddleButton, pos=tree.visualItemRect(category).center())
    QTest.mouseClick(tree.viewport(), Qt.MiddleButton, pos=tree.viewport().rect().bottomRight())
    assert owner.removals == []
    assert owner.activations == []


def action(menu, name):
    menu._test_actions = menu.actions()
    result = next((entry for entry in menu._test_actions if entry.text() == name), None)
    assert result is not None, f'missing action: {name}'
    return result


def category_item(owner, category_id):
    return owner.tree._category_items[category_id]


def point_for(owner, item):
    owner.tree.scrollToItem(item)
    QApplication.processEvents()
    return owner.tree.visualItemRect(item).center()


def mock_menu_exec(monkeypatch, execute):
    def create_menu(*args):
        menu = QMenu(*args)
        menu.exec = lambda *_args: execute(menu)
        return menu

    monkeypatch.setattr(file_tree, 'QMenu', create_menu)


def right_click_menu(owner, item, monkeypatch, execute=None):
    captured = []
    failures = []

    def capture(menu, *_args):
        captured.append(menu)
        if execute:
            try:
                execute(menu)
            except Exception as error:
                failures.append(error)

    mock_menu_exec(monkeypatch, capture)
    position = point_for(owner, item) if item is not None else owner.tree.viewport().rect().bottomRight()
    QTest.mouseClick(owner.tree.viewport(), Qt.RightButton, pos=position)
    # offscreen 不保证鼠标右击生成平台菜单事件，补发同位置的真实菜单事件。
    if not captured:
        event = QContextMenuEvent(QContextMenuEvent.Mouse, position,
                                  owner.tree.viewport().mapToGlobal(position))
        QApplication.sendEvent(owner.tree.viewport(), event)
    assert len(captured) == 1
    if failures:
        raise failures[0]
    return captured[0]


@pytest.mark.parametrize('category_id', ['', 'work'])
def test_double_click_category_commits_inline_rename(owner, category_id):
    item = category_item(owner, category_id)
    point = point_for(owner, item)
    QTest.mouseClick(owner.tree.viewport(), Qt.LeftButton, pos=point)
    QTest.mouseDClick(owner.tree.viewport(), Qt.LeftButton, pos=point)
    QApplication.processEvents()
    editor = owner.tree.findChild(QLineEdit)
    assert editor is not None and editor.isVisible()
    editor.setText('  新名称  ')
    QTest.keyClick(editor, Qt.Key_Return)
    QApplication.processEvents()
    assert owner.renames == [(category_id, '  新名称  ')]
    assert category_item(owner, category_id).text(0) == '新名称'
    assert owner.activations == []


@pytest.mark.parametrize('category_id,invalid', [('', '工作'), ('work', '默认分类'), ('work', '   ')])
def test_rejected_inline_rename_restores_category_label(owner, category_id, invalid):
    item = category_item(owner, category_id)
    previous = item.text(0)
    item.setText(0, invalid)
    assert owner.renames == [(category_id, invalid)]
    assert item.text(0) == previous


def test_refresh_and_count_changes_do_not_rename_categories(owner):
    owner.refresh_file_list()
    category_item(owner, 'work').setText(1, '9')
    owner.tree.item_for_session(owner.sessions[0]).setText(0, 'changed display')
    assert owner.renames == []
    assert category_item(owner, '').text(0) == owner.default_category_name


def test_files_cannot_be_edited_inline(owner):
    item = owner.tree.item_for_session(owner.sessions[0])
    assert not item.flags() & Qt.ItemIsEditable
    QTest.mouseDClick(owner.tree.viewport(), Qt.LeftButton, pos=point_for(owner, item))
    QTest.keyClick(owner.tree, Qt.Key_F2)
    QApplication.processEvents()
    assert owner.tree.findChild(QLineEdit) is None


@pytest.mark.parametrize('category_id', ['', 'work'])
def test_category_count_column_cannot_be_edited(owner, category_id):
    item = category_item(owner, category_id)
    previous = item.text(1)
    point = owner.tree.visualRect(owner.tree.indexFromItem(item, 1)).center()
    QTest.mouseClick(owner.tree.viewport(), Qt.LeftButton, pos=point)
    QTest.mouseDClick(owner.tree.viewport(), Qt.LeftButton, pos=point)
    QTest.keyClick(owner.tree, Qt.Key_F2)
    QApplication.processEvents()
    assert owner.tree.findChild(QLineEdit) is None
    assert item.text(1) == previous
    assert owner.renames == []


def test_right_button_press_does_not_activate_another_document(owner):
    tree = owner.tree
    target = tree.item_for_session(owner.sessions[1])
    QTest.mousePress(tree.viewport(), Qt.RightButton, pos=point_for(owner, target))
    try:
        assert owner.current_session is owner.sessions[0]
        assert owner.activations == []
        assert target.isSelected()
    finally:
        QTest.mouseRelease(tree.viewport(), Qt.RightButton, pos=point_for(owner, target))
    assert owner.activations == []


def test_right_click_rename_targets_clicked_file_without_switching_document(owner, monkeypatch):
    target = owner.tree.item_for_session(owner.sessions[1])
    right_click_menu(owner, target, monkeypatch, lambda menu: action(menu, '重命名…').trigger())
    assert owner.current_session is owner.sessions[0]
    assert owner.activations == []
    assert target.isSelected()
    assert owner.document_renames == [owner.sessions[1]]


def test_context_menu_signal_alone_does_not_activate_target(owner, monkeypatch):
    mock_menu_exec(monkeypatch, lambda *_args: None)
    target = owner.tree.item_for_session(owner.sessions[1])
    owner.tree.customContextMenuRequested.emit(point_for(owner, target))
    assert owner.current_session is owner.sessions[0]
    assert owner.activations == []
    assert target.isSelected()


@pytest.mark.parametrize('command', ['移除', '归类'])
def test_right_click_preserves_batch_selection_for_remove_and_classify(owner, monkeypatch, command):
    tree = owner.tree
    first, second = [tree.item_for_session(session) for session in owner.sessions[:2]]
    first.setSelected(True)
    second.setSelected(True)

    def execute(menu):
        assert {session.id for session in tree.selected_sessions()} == {'first', 'second'}
        if command == '移除':
            action(menu, command).trigger()
        else:
            action(action(menu, command).menu(), owner.default_category_name).trigger()

    right_click_menu(owner, second, monkeypatch, execute)
    assert owner.current_session is owner.sessions[0]
    assert owner.activations == []
    if command == '移除':
        assert owner.removals == [['first', 'second']]
    else:
        assert owner.moves == [(['first', 'second'], '')]


def test_right_click_outside_selection_uses_only_clicked_file(owner, monkeypatch):
    owner.tree.item_for_session(owner.sessions[1]).setSelected(True)
    target = owner.tree.item_for_session(owner.sessions[2])
    right_click_menu(owner, target, monkeypatch, lambda menu: action(menu, '移除').trigger())
    assert owner.removals == [['draft']]
    assert owner.current_session is owner.sessions[0]


@pytest.mark.parametrize('session_index', [0, 2])
def test_open_document_folder_is_available_only_for_saved_files(owner, monkeypatch, session_index):
    session = owner.sessions[session_index]

    def execute(menu):
        entry = action(menu, '打开所在文件夹')
        assert entry.isEnabled() == (session.path is not None)
        entry.trigger()

    right_click_menu(owner, owner.tree.item_for_session(session), monkeypatch, execute)
    assert owner.opened_folders == ([session] if session.path is not None else [])


@pytest.mark.parametrize('target_kind,category_id', [('blank', ''), ('category', ''),
                                                   ('category', 'work'), ('file', 'work')])
def test_new_submenu_keeps_clicked_category(owner, monkeypatch, target_kind, category_id):
    item = (None if target_kind == 'blank' else
            owner.tree.item_for_session(owner.sessions[2]) if target_kind == 'file' else
            category_item(owner, category_id))

    def execute(menu):
        names = [entry.text() for entry in menu.actions()]
        assert '新建文档' not in names and '新建分类' not in names
        assert '重命名分类' not in names
        assert ('删除分类' in names) == (target_kind == 'category' and bool(category_id))
        new_menu = action(menu, '新建').menu()
        assert [entry.text() for entry in new_menu.actions()] == ['文件…', '文件分类…']
        action(new_menu, '文件…').trigger()

    right_click_menu(owner, item, monkeypatch, execute)
    assert owner.new_documents == [category_id]
    assert owner.current_session is owner.sessions[0]


def test_new_category_submenu_opens_existing_category_request(owner, monkeypatch):
    monkeypatch.setattr(QInputDialog, 'getText', lambda *_args, **_kwargs: ('资料', True))
    right_click_menu(owner, category_item(owner, 'work'), monkeypatch,
                     lambda menu: action(action(menu, '新建').menu(), '文件分类…').trigger())
    assert owner.new_categories == ['资料']


def test_keyboard_navigation_and_left_click_still_activate_documents(owner, monkeypatch):
    first, second = [owner.tree.item_for_session(session) for session in owner.sessions[:2]]
    right_click_menu(owner, second, monkeypatch)
    owner.activations.clear()
    owner.tree.setFocus()
    QTest.keyClick(owner.tree, Qt.Key_Up)
    assert owner.activations == [owner.sessions[0]]
    QTest.mouseClick(owner.tree.viewport(), Qt.LeftButton, pos=point_for(owner, second))
    assert owner.current_session is owner.sessions[1]


@pytest.mark.parametrize('category_id', ['', 'work'])
def test_category_new_document_uses_clicked_category(owner, monkeypatch, category_id):
    right_click_menu(owner, category_item(owner, category_id), monkeypatch,
                     lambda menu: action(menu, '新建文档').trigger())
    assert owner.new_documents == [category_id]
    assert owner.activations == []


@pytest.mark.parametrize('category_id,expected', [('', ['first', 'second']), ('work', ['draft'])])
def test_category_batch_selection_then_remove(owner, monkeypatch, category_id, expected):
    right_click_menu(owner, category_item(owner, category_id), monkeypatch,
                     lambda menu: action(menu, '批量选择').trigger())
    target = owner.tree.item_for_session(next(session for session in owner.sessions
                                              if session.id == expected[0]))
    right_click_menu(owner, target, monkeypatch, lambda menu: action(menu, '移除').trigger())
    assert owner.removals == [expected]
    assert owner.activations == []


def test_batch_selection_action_remains_available_on_categories(owner, monkeypatch):
    right_click_menu(owner, category_item(owner, ''), monkeypatch,
                     lambda menu: action(menu, '批量选择').trigger())
    assert {session.id for session in owner.tree.selected_sessions()} == {'first', 'second'}


def test_custom_category_delete_keeps_original_target(owner, monkeypatch):
    right_click_menu(owner, category_item(owner, 'work'), monkeypatch,
                     lambda menu: action(menu, '删除分类').trigger())
    assert owner.deleted_categories == ['work']
