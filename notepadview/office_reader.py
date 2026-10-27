"""Office suffix routing and LibreOffice PDF conversion."""
from pathlib import Path


OFFICE_SUFFIXES = frozenset({
    '.doc', '.xls', '.ppt', '.pptx', '.odt', '.ods', '.odp', '.rtf',
})

_EXPORT_FILTERS = {
    '.doc': 'writer_pdf_Export',
    '.rtf': 'writer_pdf_Export',
    '.odt': 'writer_pdf_Export',
    '.xls': 'calc_pdf_Export',
    '.ods': 'calc_pdf_Export',
    '.ppt': 'impress_pdf_Export',
    '.pptx': 'impress_pdf_Export',
    '.odp': 'impress_pdf_Export',
}


def is_office_reader(path):
    return Path(path).suffix.casefold() in OFFICE_SUFFIXES


def export_filter_for_suffix(suffix):
    try:
        return _EXPORT_FILTERS[suffix.casefold()]
    except KeyError as error:
        raise ValueError(f'不支持此 Office 格式：{suffix}') from error


def office_pdf_command(executable, source, output_dir):
    executable = Path(executable)
    source = Path(source)
    output_dir = Path(output_dir)
    profile = (output_dir / 'lo-profile').resolve().as_uri()
    filter_name = export_filter_for_suffix(source.suffix)
    return [
        str(executable),
        '--headless',
        '--nologo',
        '--nodefault',
        '--norestore',
        f'-env:UserInstallation={profile}',
        '--convert-to',
        f'pdf:{filter_name}',
        '--outdir',
        str(output_dir),
        str(source.resolve()),
    ]


def convert_office_to_pdf(source, service):
    return service.convert(
        source,
        'libreoffice',
        '.pdf',
        lambda executable, path, output_dir:
            office_pdf_command(executable, path, output_dir),
    )
