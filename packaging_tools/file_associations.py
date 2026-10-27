from pathlib import Path


EXTENSIONS = ('.md', '.markdown', '.txt', '.json', '.xml', '.sql')
PROGID = 'NotepadView.Document'
APPLICATION = r'Applications\NotepadView.exe'
CONTEXT_MENU = r'*\shell\NotepadView.Open'


def register_file_associations(target, registry):
    command = f'"{Path(target).resolve() / "NotepadView.exe"}" "%1"'
    values = [
        (PROGID + r'\shell\open\command', '', command),
        (PROGID, '', 'Notepad View 文档'),
        (APPLICATION + r'\shell\open\command', '', command),
        (APPLICATION, 'FriendlyAppName', 'Notepad View'),
        (CONTEXT_MENU, '', '用 NotepadView 打开'),
        (CONTEXT_MENU, 'Icon', str(Path(target).resolve() / 'NotepadView.exe') + ',0'),
        (CONTEXT_MENU + r'\command', '', command),
    ]
    for extension in EXTENSIONS:
        values.extend([
            (APPLICATION + r'\SupportedTypes', extension, ''),
            (extension + r'\OpenWithProgids', PROGID, ''),
        ])
    for path, name, value in values:
        with registry.CreateKey(registry.HKEY_CURRENT_USER, 'Software\\Classes\\' + path) as key:
            registry.SetValueEx(key, name, 0, registry.REG_SZ, value)
