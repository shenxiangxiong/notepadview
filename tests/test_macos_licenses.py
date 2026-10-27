from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.build_macos import collect_licenses


def test_collects_nested_notices_metadata_and_python_license(tmp_path):
    installed = tmp_path / 'installed'
    entries = [Path('PySide6/Qt/resources/qtwebengine_notices/LICENSE'),
               Path('package.dist-info/METADATA'), Path('package.dist-info/licenses/COPYING'),
               Path('package.py')]
    for entry in entries:
        target = installed / entry
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(entry))
    (installed / 'LICENSE.txt').write_text('Python license')
    distribution = SimpleNamespace(metadata={'Name': 'Example'}, files=entries,
                                   locate_file=lambda entry: installed / entry)
    output = tmp_path / 'licenses'
    with patch('scripts.build_macos.importlib.metadata.distributions', return_value=[distribution]), \
         patch('scripts.build_macos.sys.base_prefix', str(installed)):
        collect_licenses(output)
    for entry in entries[:-1]:
        assert (output / 'Example' / entry).read_text() == str(entry)
    assert not (output / 'Example' / 'package.py').exists()
    assert (output / 'Python-LICENSE.txt').read_text() == 'Python license'
