from pathlib import Path

import pytest

from notepadview.office_reader import (
    OFFICE_SUFFIXES, export_filter_for_suffix, is_office_reader, office_pdf_command,
)


@pytest.mark.parametrize('suffix', [
    '.doc', '.xls', '.ppt', '.pptx', '.odt', '.ods', '.odp', '.rtf',
])
def test_office_reader_recognizes_requested_suffixes(tmp_path, suffix):
    assert suffix in OFFICE_SUFFIXES
    assert is_office_reader(tmp_path / ('sample' + suffix.upper()))


@pytest.mark.parametrize('suffix', ['.docx', '.xlsx', '.txt', '.epub'])
def test_office_reader_leaves_other_formats_to_existing_routes(tmp_path, suffix):
    assert not is_office_reader(tmp_path / ('sample' + suffix))


@pytest.mark.parametrize(('suffix', 'filter_name'), [
    ('.doc', 'writer_pdf_Export'),
    ('.rtf', 'writer_pdf_Export'),
    ('.odt', 'writer_pdf_Export'),
    ('.xls', 'calc_pdf_Export'),
    ('.ods', 'calc_pdf_Export'),
    ('.ppt', 'impress_pdf_Export'),
    ('.pptx', 'impress_pdf_Export'),
    ('.odp', 'impress_pdf_Export'),
])
def test_office_pdf_command_selects_export_filter_and_isolates_profile(
        tmp_path, suffix, filter_name):
    source = tmp_path / ('sample with spaces' + suffix)
    source.write_bytes(b'office')
    output_dir = tmp_path / 'output'
    output_dir.mkdir()
    executable = tmp_path / 'soffice.exe'
    command = office_pdf_command(executable, source, output_dir)

    assert command[0] == str(executable)
    assert '--headless' in command
    assert '-env:UserInstallation=' + (output_dir / 'lo-profile').resolve().as_uri() in command
    assert f'pdf:{filter_name}' in command
    assert command[command.index('--outdir') + 1] == str(output_dir)
    assert command[-1] == str(source.resolve())


def test_export_filter_rejects_unknown_suffix():
    with pytest.raises(ValueError, match='不支持'):
        export_filter_for_suffix('.docx')
