from pathlib import Path


EXTENSIONS = (
    '.md', '.markdown', '.txt', '.log', '.json', '.jsonl', '.xml', '.sql',
    '.yaml', '.yml', '.html', '.htm', '.css', '.scss', '.js', '.jsx', '.ts', '.tsx',
    '.py', '.ps1', '.ini', '.toml', '.csv', '.tsv', '.pdf', '.doc', '.docx',
    '.xls', '.xlsx', '.ppt', '.pptx', '.odt', '.ods', '.odp', '.rtf',
    '.epub', '.mobi', '.azw3', '.fb2', '.png', '.jpg', '.jpeg', '.gif',
    '.bmp', '.webp', '.svg', '.ico', '.tif', '.tiff',
)
CAPABILITIES = 'NotepadView.Capabilities'
PROGID = 'NotepadView.Document'
APPLICATION = r'Applications\NotepadView.exe'
CONTEXT_MENU = r'*\shell\NotepadView.Open'


def register_file_associations(target, registry):
    command = f'"{Path(target).resolve() / "NotepadView.exe"}" "%1"'
    icon = str(Path(target).resolve() / 'NotepadView.exe') + ',0'
    # SupportedTypes 存在时 Windows 会把“打开方式”候选限制为其中的扩展名。
    try:
        registry.DeleteKey(registry.HKEY_CURRENT_USER,
                           'Software\\Classes\\' + APPLICATION + r'\SupportedTypes')
    except FileNotFoundError:
        pass
    values = [
        (PROGID + r'\DefaultIcon', '', icon),
        (CAPABILITIES, 'ApplicationName', 'Notepad View'),
        (CAPABILITIES, 'ApplicationDescription', '使用 Notepad View 打开文本、代码和文档'),
        (CAPABILITIES, 'ApplicationIcon', icon),
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
            (CAPABILITIES + r'\FileAssociations', extension, PROGID),
            (extension + r'\OpenWithProgids', PROGID, ''),
        ])
    for path, name, value in values:
        with registry.CreateKey(registry.HKEY_CURRENT_USER, 'Software\\Classes\\' + path) as key:
            registry.SetValueEx(key, name, 0, registry.REG_SZ, value)
    with registry.CreateKey(registry.HKEY_CURRENT_USER, r'Software\RegisteredApplications') as key:
        registry.SetValueEx(key, 'NotepadView', 0, registry.REG_SZ,
                            'Software\\Classes\\' + CAPABILITIES)
