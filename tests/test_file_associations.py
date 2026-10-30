from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[1]
CLASSES = r'Software\Classes'
APPLICATION = r'Applications\NotepadView.exe'
PROGID = 'NotepadView.Document'
CONTEXT_MENU = r'*\shell\NotepadView.Open'
EXTENSIONS = ('.md', '.markdown', '.txt', '.json', '.xml', '.sql')


class MemoryRegistry:
    HKEY_CURRENT_USER = 'HKCU'
    REG_SZ = 1

    def __init__(self):
        self.values = {}
        self.writes = []

    @contextmanager
    def CreateKey(self, hive, path):
        assert hive == self.HKEY_CURRENT_USER
        assert path.startswith('Software\\')
        yield path

    def DeleteKey(self, hive, path):
        entries = [item for item in self.values if item[0] == path]
        if not entries:
            raise FileNotFoundError(path)
        for item in entries:
            del self.values[item]

    def SetValueEx(self, key, name, reserved, kind, value):
        assert reserved == 0
        assert kind == self.REG_SZ
        self.values[key, name] = value
        self.writes.append((key, name))


@pytest.fixture
def associations():
    assert importlib.util.find_spec('packaging_tools.file_associations'), 'Missing association registration'
    from packaging_tools import file_associations
    return file_associations


def registered_values(associations, target):
    registry = MemoryRegistry()
    associations.register_file_associations(target, registry)
    return {item: value for item, value in registry.values.items()
            if item[0].startswith(CLASSES + '\\')}


def test_registers_current_user_open_with_candidates_without_changing_defaults(associations, tmp_path):
    registry = MemoryRegistry()
    defaults = {
        (CLASSES + r'\.txt', ''): 'OtherEditor.Text',
        (CLASSES + r'\.txt\OpenWithProgids', 'OtherEditor.Text'): '',
        (CLASSES + r'\.txt', 'Content Type'): 'text/plain',
        (r'Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.txt\UserChoice', 'ProgId'):
            'OtherEditor.Text',
    }
    registry.values.update(defaults)
    target = tmp_path / "Notepad View ' & [installed]"
    associations.register_file_associations(target, registry)
    command = f'"{target.resolve() / "NotepadView.exe"}" "%1"'
    expected = {
        (CLASSES + '\\' + APPLICATION, 'FriendlyAppName'): 'Notepad View',
        (CLASSES + '\\' + APPLICATION + r'\shell\open\command', ''): command,
        (CLASSES + '\\' + PROGID, ''): 'Notepad View 文档',
        (CLASSES + '\\' + PROGID + r'\shell\open\command', ''): command,
        (CLASSES + '\\' + CONTEXT_MENU, ''): '用 NotepadView 打开',
        (CLASSES + '\\' + CONTEXT_MENU, 'Icon'): str(target.resolve() / 'NotepadView.exe') + ',0',
        (CLASSES + '\\' + CONTEXT_MENU + r'\command', ''): command,
    }
    expected.update({
        (CLASSES + '\\' + PROGID + r'\DefaultIcon', ''): str(target.resolve() / 'NotepadView.exe') + ',0',
        (CLASSES + r'\NotepadView.Capabilities', 'ApplicationName'): 'Notepad View',
        (CLASSES + r'\NotepadView.Capabilities', 'ApplicationDescription'): '使用 Notepad View 打开文本、代码和文档',
        (CLASSES + r'\NotepadView.Capabilities', 'ApplicationIcon'): str(target.resolve() / 'NotepadView.exe') + ',0',
        (r'Software\RegisteredApplications', 'NotepadView'): CLASSES + r'\NotepadView.Capabilities',
    })
    for extension in associations.EXTENSIONS:
        expected[CLASSES + '\\' + extension + r'\OpenWithProgids', PROGID] = ''
        expected[CLASSES + r'\NotepadView.Capabilities\FileAssociations', extension] = PROGID
    assert registry.values == defaults | expected
    assert set(registry.writes) == set(expected)
    associations.register_file_associations(target, registry)
    assert registry.values == defaults | expected


def test_open_with_is_not_restricted_to_known_extensions(associations, tmp_path):
    registry = MemoryRegistry()
    registry.values[CLASSES + '\\' + APPLICATION + r'\SupportedTypes', '.txt'] = ''
    associations.register_file_associations(tmp_path, registry)
    assert not any(key.endswith(r'\SupportedTypes') for key, _ in registry.values)
    assert {'.log', '.yaml', '.py', '.pdf', '.docx', '.epub', '.png'} <= set(associations.EXTENSIONS)


def test_register_install_integrates_associations_and_hidden_shell_notification(tmp_path, monkeypatch):
    import winreg
    from packaging_tools.installer_core import register_install

    values = {}
    commands = []

    @contextmanager
    def create(hive, path):
        assert hive == winreg.HKEY_CURRENT_USER
        yield path

    monkeypatch.setattr(winreg, 'CreateKey', create)
    monkeypatch.setattr(winreg, 'DeleteKey', lambda hive, path: None)
    monkeypatch.setattr(winreg, 'SetValueEx', lambda key, name, reserved, kind, value:
                        values.__setitem__((key, name), value))
    monkeypatch.setattr(subprocess, 'run', lambda command, **kwargs: commands.append((command, kwargs)))
    target = tmp_path / "Notepad View ' & [installed]"
    register_install(target, desktop=False)
    assert values[CLASSES + '\\' + APPLICATION, 'FriendlyAppName'] == 'Notepad View'
    context_command = values[CLASSES + '\\' + CONTEXT_MENU + r'\command', '']
    assert context_command.startswith('"')
    assert context_command.endswith('" "%1"')
    assert values[CLASSES + '\\' + CONTEXT_MENU, ''] == '用 NotepadView 打开'
    assert values[CLASSES + '\\' + CONTEXT_MENU, 'Icon'].endswith('NotepadView.exe,0')
    assert len(commands) == 1
    command, options = commands[0]
    assert command[-1].startswith("$ErrorActionPreference = 'Stop'\n")
    assert 'SHChangeNotify(0x08000000, 0, [IntPtr]::Zero, [IntPtr]::Zero)' in command[-1]
    assert 'shell32.dll' in command[-1]
    assert str(target) not in command[-1]
    assert options['creationflags'] == subprocess.CREATE_NO_WINDOW
    assert options['capture_output'] and options['check']
    uninstall_command = values[r'Software\Microsoft\Windows\CurrentVersion\Uninstall\NotepadView',
                               'UninstallString']
    assert '-WindowStyle Hidden' in uninstall_command


# Evaluate function ASTs only; never run top-level uninstall code. The injected
# registry root is a random test key outside the real Software\Classes tree.
UNINSTALL_HARNESS = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($env:NV_UNINSTALL, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$functions = @($ast.FindAll({ param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst]
}, $false))
foreach ($definition in $functions) { Invoke-Expression $definition.Extent.Text }
if (-not (Get-Command Remove-FileAssociations -ErrorAction SilentlyContinue)) {
    throw 'Missing association cleanup'
}
$testPath = 'Software\NotepadView.AssociationTests\' + $env:NV_TEST_ID
if ($env:NV_TEST_ID -notmatch '^[a-f0-9]{32}$') { throw 'Invalid isolated registry key' }
$root = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($testPath)
try {
    foreach ($entry in ($env:NV_VALUES | ConvertFrom-Json)) {
        $key = $root.CreateSubKey($entry[0])
        try { $key.SetValue($entry[1], $entry[2], [Microsoft.Win32.RegistryValueKind]::String) }
        finally { $key.Dispose() }
    }
    $registered = $root.CreateSubKey('TestRegisteredApplications')
    try {
        $registered.SetValue('NotepadView', 'Software\Classes\NotepadView.Capabilities')
        $registered.SetValue('OtherEditor', 'Software\OtherEditor\Capabilities')
        $changed = Remove-FileAssociations -InstallRoot $env:NV_TARGET -Classes $root -RegisteredApplications $registered
        $secondChange = Remove-FileAssociations -InstallRoot $env:NV_TARGET -Classes $root -RegisteredApplications $registered
        $registeredValue = $registered.GetValue('NotepadView')
        if ($registered.GetValue('OtherEditor') -ne 'Software\OtherEditor\Capabilities') {
            throw 'Other application registration changed'
        }
    } finally { $registered.Dispose() }
    $root.DeleteSubKeyTree('TestRegisteredApplications')
    function Read-TestTree($Key, [string]$Prefix) {
        $keys.Add($Prefix)
        foreach ($name in $Key.GetValueNames()) {
            $values.Add(@($Prefix, $name, $Key.GetValue($name)))
        }
        foreach ($child in $Key.GetSubKeyNames()) {
            $opened = $Key.OpenSubKey($child)
            try { Read-TestTree $opened ($Prefix + '\' + $child).TrimStart('\') }
            finally { $opened.Dispose() }
        }
    }
    $values = [Collections.Generic.List[object]]::new()
    $keys = [Collections.Generic.List[string]]::new()
    Read-TestTree $root ''
    @{ Changed = $changed; SecondChange = $secondChange; RegisteredValue = $registeredValue; Values = @($values.ToArray());
       Keys = @($keys.ToArray()) } | ConvertTo-Json -Depth 10 -Compress
} finally {
    $root.Dispose()
    [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree($testPath, $false)
}
'''


def uninstall_values(values, target):
    assert all(key.startswith(CLASSES + '\\') for key, _ in values)
    entries = [[key[len(CLASSES) + 1:], name, value] for (key, name), value in values.items()]
    completed = subprocess.run([
        'powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-Command', UNINSTALL_HARNESS,
    ], env=dict(os.environ, NV_UNINSTALL=str(ROOT / 'packaging_tools/uninstall.ps1'),
                NV_TEST_ID=uuid.uuid4().hex, NV_TARGET=str(target.resolve()),
                NV_VALUES=json.dumps(entries)), capture_output=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW)
    assert completed.returncode == 0, completed.stderr.decode('utf-8', errors='replace')
    assert completed.stderr == b''
    result = json.loads(completed.stdout)
    result['Values'] = {(CLASSES + '\\' + key, name): value for key, name, value in result['Values']}
    return result


def test_uninstall_removes_own_registration_but_preserves_other_values(associations, tmp_path):
    values = registered_values(associations, tmp_path)
    preserved = {
        (CLASSES + r'\.txt', ''): 'OtherEditor.Text',
        (CLASSES + r'\.txt', 'Content Type'): 'text/plain',
        (CLASSES + r'\.txt\OpenWithProgids', 'OtherEditor.Text'): '',
        (CLASSES + r'\.txt\UserChoice', 'ProgId'): 'OtherEditor.Text',
        (CLASSES + r'\Applications\OtherEditor.exe\shell\open\command', ''): 'other command',
    }
    result = uninstall_values(values | preserved, tmp_path)
    assert result['Values'] == preserved
    assert result['Changed'] is True
    assert result['SecondChange'] is False
    assert PROGID not in result['Keys']
    assert APPLICATION not in result['Keys']
    assert '.md\\OpenWithProgids' not in result['Keys']
    assert CONTEXT_MENU not in result['Keys']
    assert result['RegisteredValue'] is None


def test_old_uninstall_preserves_new_installation(associations, tmp_path):
    old, new = tmp_path / 'old install', tmp_path / 'new install'
    values = registered_values(associations, old) | registered_values(associations, new)
    result = uninstall_values(values, old)
    assert result['Values'] == values
    assert result['RegisteredValue'] == CLASSES + r'\NotepadView.Capabilities'
    assert result['Changed'] is False
    assert result['SecondChange'] is False


@pytest.mark.parametrize('changed_root', [APPLICATION, PROGID])
def test_uninstall_checks_application_and_progid_ownership_independently(associations, tmp_path, changed_root):
    values = registered_values(associations, tmp_path)
    key = CLASSES + '\\' + changed_root + r'\shell\open\command'
    values[key, ''] = '"C:\\Other Install\\NotepadView.exe" "%1"'
    preserved = {item: value for item, value in values.items()
                 if (changed_root == PROGID and item[0].startswith(CLASSES + r'\NotepadView.Capabilities'))
                 or item[0] == CLASSES + '\\' + changed_root
                 or item[0].startswith(CLASSES + '\\' + changed_root + '\\')
                 or (changed_root == PROGID and item[1] == PROGID)}
    result = uninstall_values(values, tmp_path)
    assert result['Values'] == preserved
    assert result['Changed'] is True


def test_uninstall_preserves_modified_values_and_unknown_subkeys(associations, tmp_path):
    values = registered_values(associations, tmp_path)
    preserved = {
        (CLASSES + '\\' + APPLICATION, 'FriendlyAppName'): 'Customized name',
        (CLASSES + '\\' + APPLICATION + r'\SupportedTypes', '.log'): '',
        (CLASSES + '\\' + APPLICATION + r'\SupportedTypes', '.sql'): 'changed',
        (CLASSES + '\\' + PROGID + r'\shell\edit\command', ''): 'other verb',
        (CLASSES + r'\.md\OpenWithProgids', PROGID): 'changed',
        (CLASSES + '\\' + CONTEXT_MENU, 'CustomValue'): 'keep',
        (CLASSES + '\\' + CONTEXT_MENU, ''): '自定义菜单名称',
        (CLASSES + '\\' + CONTEXT_MENU, 'Icon'): 'C:\\Custom\\icon.exe,0',
        (CLASSES + '\\' + CONTEXT_MENU + r'\extended', 'CustomValue'): 'keep',
    }
    result = uninstall_values(values | preserved, tmp_path)
    assert result['Values'] == preserved
    assert APPLICATION in result['Keys'] and PROGID in result['Keys']
    assert CONTEXT_MENU in result['Keys']


def test_uninstall_matches_windows_path_case_insensitively(associations, tmp_path):
    values = registered_values(associations, tmp_path)
    result = uninstall_values(values, Path(str(tmp_path).upper()))
    assert result['Values'] == {}
    assert result['Changed'] is True


def test_no_register_skips_association_cleanup_and_notification(tmp_path):
    target = tmp_path / 'app'
    target.mkdir()
    uninstall = target / 'uninstall.ps1'
    uninstall.write_bytes((ROOT / 'packaging_tools/uninstall.ps1').read_bytes())
    (target / 'notepadview-install.json').write_text(json.dumps({
        'app': 'NotepadView', 'files': ['uninstall.ps1'],
    }), encoding='utf-8')
    command = r'''
$ErrorActionPreference = 'Stop'
function Deny-Registration { throw 'Registration must be skipped' }
Set-Alias Remove-FileAssociations Deny-Registration
Set-Alias Send-AssociationChangeNotification Deny-Registration
Set-Alias New-Object Deny-Registration
& $env:NV_UNINSTALL -Silent -NoRegister
exit $LASTEXITCODE
'''
    completed = subprocess.run([
        'powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-Command', command,
    ], env=dict(os.environ, NV_UNINSTALL=str(uninstall)), capture_output=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW)
    assert completed.returncode == 0, completed.stderr.decode('utf-8', errors='replace')
    assert completed.stdout == completed.stderr == b''
    assert not target.exists()
