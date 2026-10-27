from pathlib import Path
import zipfile

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtWebEngineCore import QWebEngineSettings

from notepadview.ebook_reader import EbookReadError, read_epub
from notepadview.ebook_reader_widget import EbookReader
from test_ui import app  # noqa: F401


def write_epub(path, *, extra_entries=None, replacement=None):
    files = {
        'mimetype': b'application/epub+zip',
        'META-INF/container.xml': '''<?xml version="1.0"?>
            <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
              <rootfiles><rootfile full-path="EPUB/package.opf"
                media-type="application/oebps-package+xml"/></rootfiles>
            </container>''',
        'EPUB/package.opf': '''<?xml version="1.0"?>
            <package xmlns="http://www.idpf.org/2007/opf" version="3.0"
                     unique-identifier="book-id">
              <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
                <dc:identifier id="book-id">urn:test:book</dc:identifier>
                <dc:title>测试书</dc:title>
              </metadata>
              <manifest>
                <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml"
                      properties="nav"/>
                <item id="one" href="text/one.xhtml" media-type="application/xhtml+xml"/>
                <item id="two" href="text/two.xhtml" media-type="application/xhtml+xml"/>
                <item id="image" href="images/cover.png" media-type="image/png"/>
              </manifest>
              <spine><itemref idref="one"/><itemref idref="two"/></spine>
            </package>''',
        'EPUB/nav.xhtml': '''<html xmlns="http://www.w3.org/1999/xhtml"
            xmlns:epub="http://www.idpf.org/2007/ops"><body>
              <nav epub:type="toc"><ol>
                <li><a href="text/one.xhtml#start">第一章</a></li>
                <li><a href="text/two.xhtml">第二章</a></li>
              </ol></nav></body></html>''',
        'EPUB/text/one.xhtml': '<html><body><h1 id="start">第一章</h1></body></html>',
        'EPUB/text/two.xhtml': '<html><body><h1>第二章</h1></body></html>',
        'EPUB/images/cover.png': b'png-image',
    }
    if replacement:
        files.update(replacement)
    if extra_entries:
        files.update(extra_entries)
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('mimetype', files.pop('mimetype'), compress_type=zipfile.ZIP_STORED)
        for name, data in files.items():
            if isinstance(data, str):
                data = data.encode('utf-8')
            archive.writestr(name, data)


def test_reads_epub_title_spine_navigation_and_local_resources(tmp_path):
    source = tmp_path / 'sample.epub'
    write_epub(source)

    book = read_epub(source, tmp_path / 'cache')

    assert book.title == '测试书'
    assert [chapter.title for chapter in book.chapters] == ['第一章', '第二章']
    assert [(entry.title, entry.chapter, entry.fragment) for entry in book.toc] == [
        ('第一章', 0, 'start'), ('第二章', 1, ''),
    ]
    assert (book.content_root / 'EPUB/images/cover.png').read_bytes() == b'png-image'
    assert book.chapters[0].relative_path == Path('EPUB/text/one.xhtml')


@pytest.mark.parametrize('replacement', [
    {'EPUB/package.opf': b'<!DOCTYPE package [<!ENTITY x SYSTEM "file:///secret">]><package/>'},
])
def test_rejects_epub_xml_with_entities(tmp_path, replacement):
    source = tmp_path / 'unsafe.epub'
    write_epub(source, replacement=replacement)

    with pytest.raises(EbookReadError):
        read_epub(source, tmp_path / 'cache')


@pytest.mark.parametrize('member', ['../outside.txt', '/absolute.txt', 'EPUB/../../outside.txt'])
def test_rejects_archive_path_traversal(tmp_path, member):
    source = tmp_path / 'traversal.epub'
    write_epub(source, extra_entries={member: b'outside'})

    with pytest.raises(EbookReadError, match='路径'):
        read_epub(source, tmp_path / 'cache')


def test_rejects_entry_count_and_expanded_size_limits(tmp_path):
    source = tmp_path / 'oversized.epub'
    write_epub(source, extra_entries={'EPUB/extra.txt': b'x' * 100})

    with pytest.raises(EbookReadError, match='数量'):
        read_epub(source, tmp_path / 'entry-cache', max_entries=2)
    with pytest.raises(EbookReadError, match='大小'):
        read_epub(source, tmp_path / 'size-cache', max_expanded_bytes=50)


def test_rejects_corrupt_or_incomplete_epub(tmp_path):
    corrupt = tmp_path / 'corrupt.epub'
    corrupt.write_bytes(b'not a zip archive')
    incomplete = tmp_path / 'incomplete.epub'
    with zipfile.ZipFile(incomplete, 'w') as archive:
        archive.writestr('mimetype', 'application/epub+zip')

    for source in (corrupt, incomplete):
        with pytest.raises(EbookReadError):
            read_epub(source, tmp_path / 'cache')


def test_reader_navigates_chapters_and_enforces_local_readonly_policy(
        app, tmp_path):
    source = tmp_path / 'sample.epub'
    write_epub(source)
    book = read_epub(source, tmp_path / 'cache')
    reader = EbookReader()
    try:
        reader.load_book(book, {'chapter': 1, 'scroll': 420, 'zoom': 1.2})
        assert reader.toc_list.count() == 2
        assert reader.current_chapter == 1
        assert reader.state() == {'chapter': 1, 'scroll': 420.0, 'zoom': 1.2}
        assert reader.view.settings().testAttribute(
            QWebEngineSettings.JavascriptEnabled) is False
        assert reader.view.settings().testAttribute(
            QWebEngineSettings.LocalContentCanAccessRemoteUrls) is False
        assert reader._is_allowed_resource(
            QUrl.fromLocalFile(str(book.content_root / 'EPUB/text/one.xhtml')))
        assert not reader._is_allowed_resource(QUrl('https://example.invalid/book.js'))
        assert not reader._is_allowed_resource(
            QUrl.fromLocalFile(str(tmp_path / 'outside.html')))

        reader.next_button.click()
        assert reader.current_chapter == 1
        assert reader.state()['chapter'] == 1
        assert reader.state()['zoom'] == 1.2
        reader.previous_button.click()
        assert reader.current_chapter == 0
    finally:
        reader.close()
