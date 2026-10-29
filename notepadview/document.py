import os
from pathlib import Path
import tempfile
import locale
import re


_UNSAFE_TEXT = re.compile(r'[\x00-\x08\x0b\x0e-\x1f\x7f-\x9f\ufffd]')


ENCODING_LABELS = {
    'ansi': 'ANSI', 'utf-8': 'UTF-8', 'utf-8-sig': 'UTF-8BOM',
    'utf-16-be': 'UTF-16 BE BOM', 'utf-16': 'UTF-16 LE BOM',
}


def ansi_encoding() -> str:
    if os.name == 'nt':
        import ctypes
        return 'cp' + str(ctypes.windll.kernel32.GetACP())
    return locale.getpreferredencoding(False)


def encode_text(text: str, encoding: str) -> bytes:
    if encoding in ('utf-16-be', 'utf-32-be'):
        text = '\ufeff' + text
    if encoding == 'utf-16':
        return b'\xff\xfe' + text.encode('utf-16-le', errors='strict')
    return text.encode(ansi_encoding() if encoding == 'ansi' else encoding, errors='strict')


class BinaryDocumentError(ValueError):
    pass


def load_text_document(path: Path, encoding: str | None = None) -> tuple[str, str, str]:
    if Path(path).suffix.casefold() in {
            '.exe', '.dll', '.com', '.msi', '.bin', '.zip', '.7z', '.rar', '.gz',
            '.bz2', '.xz', '.tar', '.iso', '.mp3', '.mp4', '.m4a', '.m4v', '.wav',
            '.flac', '.ogg', '.opus', '.avi', '.mov', '.mkv', '.wmv', '.webm', '.wma'}:
        raise BinaryDocumentError('暂不支持此二进制文件类型。')
    data = Path(path).read_bytes()
    if data.startswith((b'PK\x03\x04', b'%PDF-', b'\x7fELF', b'\x1f\x8b', b'Rar!',
                        b'7z\xbc\xaf\x27\x1c', b'RIFF', b'ID3', b'fLaC', b'OggS')) or data[4:8] == b'ftyp':
        raise BinaryDocumentError('该文件不是可安全编辑的文本。')
    forced = encoding is not None
    if forced:
        encodings = [encoding]
    elif data.startswith(b'\x00\x00\xfe\xff'):
        encodings = ['utf-32-be']
    elif data.startswith(b'\xff\xfe\x00\x00'):
        encodings = ['utf-32']
    elif data.startswith(b'\xfe\xff'):
        encodings = ['utf-16-be']
    elif data.startswith(b'\xff\xfe'):
        encodings = ['utf-16']
    elif data.startswith(b'\xef\xbb\xbf'):
        encodings = ['utf-8-sig']
    else:
        encodings = ['utf-8', 'ansi', 'gb18030']
    for encoding in encodings:
        try:
            codec = ansi_encoding() if encoding == 'ansi' else encoding
            payload = data
            if forced and encoding in ('utf-16', 'utf-16-be'):
                bom = b'\xff\xfe' if encoding == 'utf-16' else b'\xfe\xff'
                opposite = b'\xfe\xff' if encoding == 'utf-16' else b'\xff\xfe'
                if data.startswith(opposite):
                    raise UnicodeError('文件 BOM 与指定的 UTF-16 字节序不一致。')
                codec = 'utf-16-le' if encoding == 'utf-16' else encoding
                payload = data[len(bom):] if data.startswith(bom) else data
            text = payload.decode(codec, errors='strict')
        except UnicodeError:
            if forced:
                raise
            continue
        if _UNSAFE_TEXT.search(text):
            continue
        # 只有严格往返一致的编码才允许编辑，未知二进制仅提示不支持。
        if not forced and text.encode(codec) != data:
            continue
        if (encoding == 'utf-32-be' or (encoding == 'utf-16-be' and not forced)) and text.startswith('\ufeff'):
            text = text[1:]
        newline = '\r\n' if '\r\n' in text and '\n' not in text.replace('\r\n', '') else '\r' if '\r' in text and '\n' not in text else '\n'
        return text.replace('\r\n', '\n').replace('\r', '\n'), encoding, newline
    raise BinaryDocumentError('无法可靠识别文本编码，该文件可能是二进制文件。')


def read_document(path: Path) -> str:
    return load_text_document(path)[0]


def save_document(path: Path, text: str, encoding='utf-8', newline='\n') -> None:
    path = Path(path)
    text = text.replace('\n', newline)
    data = encode_text(text, encoding)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name,
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
