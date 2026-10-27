from contextlib import nullcontext
from pathlib import Path
import ast
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('desktop', [False, True])
def test_registration_uses_installed_executable_for_display_and_shortcut_icons(
        tmp_path, monkeypatch, desktop):
    import winreg
    from packaging_tools.installer_core import EXECUTABLE_NAME, register_install

    values = {}
    commands = []
    monkeypatch.setattr(winreg, 'CreateKey', lambda *args: nullcontext(None))
    monkeypatch.setattr(winreg, 'SetValueEx', lambda key, name, reserved, kind, value:
                        values.__setitem__(name, value))
    monkeypatch.setattr(subprocess, 'run', lambda command, **kwargs: commands.append((command, kwargs)))
    target = tmp_path / "Notepad View ' installed"
    register_install(target, desktop=desktop)

    assert values['DisplayIcon'] == str(target.resolve() / EXECUTABLE_NAME)
    assert values['InstallLocation'] == str(target.resolve())
    command, options = commands[0]
    assert options['env']['NOTEPADVIEW_TARGET'] == str(target.resolve())
    assert options['env']['NOTEPADVIEW_DESKTOP'] == ('1' if desktop else '0')
    assert options['env']['NOTEPADVIEW_EXECUTABLE'] == EXECUTABLE_NAME
    assert '$link.TargetPath = Join-Path $env:NOTEPADVIEW_TARGET $env:NOTEPADVIEW_EXECUTABLE' in command[-1]
    assert '$link.IconLocation = $link.TargetPath + ",0"' in command[-1]
    assert '$link.WorkingDirectory = $env:NOTEPADVIEW_TARGET' in command[-1]
    assert 'GetFolderPath(\'Programs\')' in command[-1]
    assert 'GetFolderPath(\'Desktop\')' in command[-1]
    assert str(target) not in command[-1]
    assert options['check'] and options['capture_output']
    assert options['creationflags'] == subprocess.CREATE_NO_WINDOW


def test_installer_header_places_packaged_png_before_product_title_without_gui():
    source = (ROOT / 'packaging_tools' / 'setup.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    window = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                  and node.name == 'SetupWindow')
    constructor = next(node for node in window.body if isinstance(node, ast.FunctionDef)
                       and node.name == '__init__')
    calls = [node for node in ast.walk(constructor) if isinstance(node, ast.Call)]
    pixmaps = [node for node in calls if isinstance(node.func, ast.Name) and node.func.id == 'QPixmap']
    assert pixmaps, 'Installer header must load the packaged app.png'
    assert 'app.png' in ast.unparse(pixmaps[0])
    assert 'RESOURCES' in ast.unparse(pixmaps[0])
    assert any(isinstance(node.func, ast.Attribute) and node.func.attr == 'setPixmap' for node in calls)
    header_widgets = [ast.unparse(node.args[0]) for node in sorted(calls, key=lambda node: node.lineno)
                      if isinstance(node.func, ast.Attribute) and node.func.attr == 'addWidget'
                      and ast.unparse(node.func.value) == 'header']
    assert header_widgets[:2] == ['self.logo', 'title']
