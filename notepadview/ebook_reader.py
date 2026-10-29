"""Bounded EPUB package validation and chapter/table-of-contents extraction."""
from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import stat
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile


_CONTAINER = 'META-INF/container.xml'
_MAX_ARCHIVE_ENTRIES = 4096
_MAX_EXPANDED_BYTES = 128 * 1024 * 1024
_EPUB_NS = 'http://www.idpf.org/2007/ops'
EBOOK_CONVERT_SUFFIXES = frozenset({'.mobi', '.azw3', '.fb2'})


class EbookReadError(ValueError):
    pass


@dataclass(frozen=True)
class EpubChapter:
    title: str
    relative_path: Path


@dataclass(frozen=True)
class EpubTocEntry:
    title: str
    chapter: int
    fragment: str = ''


@dataclass(frozen=True)
class EpubBook:
    title: str
    chapters: tuple[EpubChapter, ...]
    toc: tuple[EpubTocEntry, ...]
    content_root: Path


def ebook_convert_command(executable, source, output_dir):
    executable = Path(executable)
    source = Path(source)
    output_dir = Path(output_dir)
    target = output_dir / (source.stem + '.epub')
    return [str(executable), str(source.resolve()), str(target)]


def convert_ebook_to_epub(source, service):
    return service.convert(
        source,
        'calibre',
        '.epub',
        ebook_convert_command,
    )


def _safe_member_path(name):
    if not name or '\x00' in name or '\\' in name or re.match(r'^[A-Za-z]:', name):
        raise EbookReadError(f'EPUB 包含无效路径：{name!r}')
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ('', '.', '..') for part in path.parts):
        raise EbookReadError(f'EPUB 包含不安全路径：{name}')
    return path


def validate_epub_archive(path, max_entries, max_expanded_bytes):
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as error:
        raise EbookReadError('EPUB 文件无效或损坏。') from error
    try:
        infos = archive.infolist()
        if not infos or infos[0].filename != 'mimetype':
            raise EbookReadError('EPUB 缺少位于首位的 mimetype 文件。')
        if len(infos) > max_entries:
            raise EbookReadError('EPUB 文件数量超过允许上限。')
        members = {}
        expanded = 0
        for info in infos:
            member = _safe_member_path(info.filename.rstrip('/') if info.is_dir() else info.filename)
            normalized = member.as_posix()
            if normalized.casefold() in members:
                raise EbookReadError(f'EPUB 包含重复路径：{normalized}')
            members[normalized.casefold()] = info
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise EbookReadError(f'EPUB 不允许符号链接：{normalized}')
            if info.flag_bits & 1:
                raise EbookReadError(f'EPUB 包含加密部件：{normalized}')
            if info.file_size > max(1, info.compress_size) * 1000:
                raise EbookReadError('EPUB 单个部件压缩比超过允许上限。')
            expanded += info.file_size
            if expanded > max_expanded_bytes:
                raise EbookReadError('EPUB 解压后大小超过允许上限。')
        if archive.read('mimetype') != b'application/epub+zip':
            raise EbookReadError('EPUB mimetype 内容无效。')
        return archive, members
    except EbookReadError:
        archive.close()
        raise
    except (KeyError, RuntimeError, zipfile.BadZipFile) as error:
        archive.close()
        raise EbookReadError('EPUB 文件无效或损坏。') from error


def _read_xml(archive, members, name):
    info = members.get(name.casefold())
    if info is None or info.is_dir():
        raise EbookReadError(f'EPUB 缺少必要文件：{name}')
    payload = archive.read(info)
    # HTML5's plain doctype declares no entities or external resources.
    lowered = re.sub(br'<!DOCTYPE\s+html\s*>', b'', payload, flags=re.IGNORECASE).lower()
    if b'<!doctype' in lowered or b'<!entity' in lowered:
        raise EbookReadError('EPUB XML 不允许 DTD 或实体声明。')
    try:
        return ET.fromstring(payload)
    except ET.ParseError as error:
        raise EbookReadError(f'EPUB XML 格式无效：{name}') from error


def _local_name(tag):
    return tag.rsplit('}', 1)[-1]


def _resolve_href(base_file, href, members):
    parsed = urllib.parse.urlsplit(href)
    if parsed.scheme or parsed.netloc:
        return None, parsed.fragment
    decoded = urllib.parse.unquote(parsed.path)
    if '\\' in decoded or '\x00' in decoded:
        raise EbookReadError(f'EPUB 包含不安全资源链接：{href}')
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(base_file), decoded))
    if resolved in ('', '.') or resolved == '..' or resolved.startswith('../') or resolved.startswith('/'):
        raise EbookReadError(f'EPUB 包含越界资源链接：{href}')
    if resolved.casefold() not in members:
        return None, parsed.fragment
    return resolved, urllib.parse.unquote(parsed.fragment)


def parse_container(archive, members):
    root = _read_xml(archive, members, _CONTAINER)
    for element in root.iter():
        if _local_name(element.tag) == 'rootfile':
            path = element.get('full-path', '')
            resolved = _safe_member_path(path).as_posix()
            if resolved.casefold() in members:
                return resolved
    raise EbookReadError('EPUB 找不到有效的 package.opf。')


def _manifest(package, package_path, members):
    items = {}
    by_path = {}
    for element in package.iter():
        if _local_name(element.tag) != 'item':
            continue
        identifier = element.get('id')
        href = element.get('href')
        if not identifier or not href:
            continue
        resolved, _ = _resolve_href(package_path, href, members)
        if resolved is None:
            continue
        item = {
            'path': resolved,
            'media_type': element.get('media-type', '').casefold(),
            'properties': set(element.get('properties', '').split()),
        }
        items[identifier] = item
        by_path[resolved.casefold()] = item
    return items, by_path


def _navigation_entries(root, base_file, members):
    entries = []
    for nav in root.iter():
        if _local_name(nav.tag) != 'nav':
            continue
        nav_type = nav.get(f'{{{_EPUB_NS}}}type', nav.get('type', ''))
        if 'toc' not in nav_type.split():
            continue
        for anchor in nav.iter():
            if _local_name(anchor.tag) != 'a' or not anchor.get('href'):
                continue
            target, fragment = _resolve_href(base_file, anchor.get('href'), members)
            title = ' '.join(''.join(anchor.itertext()).split())
            if target and title:
                entries.append((title, target.casefold(), fragment))
        if entries:
            return entries
    return entries


def _ncx_entries(root, base_file, members):
    entries = []

    def visit(element):
        for child in element:
            if _local_name(child.tag) == 'navPoint':
                label = next((' '.join(''.join(node.itertext()).split())
                              for node in child.iter()
                              if _local_name(node.tag) == 'text'), '')
                content = next((node.get('src') for node in child.iter()
                                if _local_name(node.tag) == 'content' and node.get('src')), None)
                if content:
                    target, fragment = _resolve_href(base_file, content, members)
                    if target and label:
                        entries.append((label, target.casefold(), fragment))
            visit(child)

    visit(root)
    return entries


def extract_validated_entries(archive, members, cache_root, digest):
    root = Path(cache_root).resolve() / 'epub' / digest
    root.mkdir(parents=True, exist_ok=True)
    resolved_root = root.resolve()
    for name, info in members.items():
        if info.is_dir():
            continue
        path = PurePosixPath(info.filename)
        destination = root.joinpath(*path.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            destination.resolve().relative_to(resolved_root)
        except ValueError as error:
            raise EbookReadError(f'EPUB 解压路径越出缓存目录：{info.filename}') from error
        with archive.open(info, 'r') as source, destination.open('wb') as target:
            shutil.copyfileobj(source, target, 1024 * 1024)
    return root


def build_epub_book(archive, members, package_path, content_root):
    package = _read_xml(archive, members, package_path)
    items, by_path = _manifest(package, package_path, members)
    title = next((' '.join(''.join(element.itertext()).split())
                  for element in package.iter() if _local_name(element.tag) == 'title'), '')
    spine = next((element for element in package.iter()
                  if _local_name(element.tag) == 'spine'), None)
    if spine is None:
        raise EbookReadError('EPUB 缺少阅读顺序（spine）。')
    chapters = []
    chapter_by_path = {}
    for reference in spine:
        if _local_name(reference.tag) != 'itemref':
            continue
        item = items.get(reference.get('idref'))
        if item is None or item['media_type'] not in ('application/xhtml+xml', 'text/html'):
            continue
        if 'nav' in item['properties']:
            continue
        chapter_by_path[item['path'].casefold()] = len(chapters)
        chapters.append(EpubChapter(Path(item['path']).stem, Path(item['path'])))
    if not chapters:
        raise EbookReadError('EPUB 没有可阅读的章节。')

    toc = []
    nav_item = next((item for item in items.values() if 'nav' in item['properties']), None)
    nav_ncx = next((item for item in items.values()
                    if item['media_type'] == 'application/x-dtbncx+xml'), None)
    nav_item = nav_item or nav_ncx
    if nav_item:
        nav_root = _read_xml(archive, members, nav_item['path'])
        raw_entries = (_navigation_entries(nav_root, nav_item['path'], members)
                       if nav_item['media_type'] == 'application/xhtml+xml'
                       else _ncx_entries(nav_root, nav_item['path'], members))
        for label, path, fragment in raw_entries:
            chapter = chapter_by_path.get(path)
            if chapter is not None:
                toc.append(EpubTocEntry(label, chapter, fragment))
    for entry in toc:
        chapter = chapters[entry.chapter]
        if chapter.title == Path(chapter.relative_path).stem:
            chapters[entry.chapter] = EpubChapter(entry.title, chapter.relative_path)
    if not toc:
        toc = [EpubTocEntry(chapter.title, index)
               for index, chapter in enumerate(chapters)]
    return EpubBook(title or '未命名电子书', tuple(chapters), tuple(toc), content_root)


def read_epub(path, cache_root, max_entries=_MAX_ARCHIVE_ENTRIES,
              max_expanded_bytes=_MAX_EXPANDED_BYTES):
    path = Path(path)
    if not path.is_file():
        raise EbookReadError(f'电子书文件不存在：{path}')
    try:
        archive, members = validate_epub_archive(path, max_entries, max_expanded_bytes)
    except (OSError, zipfile.BadZipFile) as error:
        raise EbookReadError('EPUB 文件无效或无法读取。') from error
    try:
        package_path = parse_container(archive, members)
        _read_xml(archive, members, package_path)
        digest_hash = sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest_hash.update(chunk)
        digest = digest_hash.hexdigest()
        content_root = extract_validated_entries(archive, members, cache_root, digest)
        return build_epub_book(archive, members, package_path, content_root)
    finally:
        archive.close()
