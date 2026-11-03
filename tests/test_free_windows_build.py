from pathlib import Path

from scripts import build as windows_build


ROOT = Path(__file__).resolve().parents[1]


def test_windows_build_excludes_legacy_pdf_backend(monkeypatch):
    commands = []
    monkeypatch.setattr(windows_build, 'prepare_windows_runtimes', lambda root: {})
    monkeypatch.setattr(windows_build, 'run', lambda *args: commands.append(args))
    monkeypatch.setattr(windows_build, 'assemble_setup', lambda **kwargs: None)
    windows_build.build()
    command = next(args for args in commands if args[:2] == ('-m', 'PyInstaller'))
    excluded = {command[index + 1] for index, value in enumerate(command)
                if value == '--exclude-module'}
    assert {'pymupdf', 'fitz', 'notepadview.pdf_editor', 'notepadview.pdf_model'} <= excluded
    assert '--noupx' in command


def test_default_dependencies_do_not_require_pymupdf():
    requirements = (ROOT / 'requirements.txt').read_text(encoding='utf-8')
    assert not any(line.lower().startswith('pymupdf') for line in requirements.splitlines())
