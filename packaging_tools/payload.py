"""安装载荷的无损压缩与兼容读取。"""
from pathlib import Path
import tarfile
import zipfile


def write_payload(distribution, destination):
    with tarfile.open(destination, 'w:xz', preset=6) as archive:
        for path in sorted(Path(distribution).rglob('*')):
            if path.is_file():
                archive.add(path, arcname=path.relative_to(distribution).as_posix(), recursive=False)


class SolidPayload:
    def __init__(self, path):
        self.archive = tarfile.open(path, 'r:xz')

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.archive.close()

    def infolist(self):
        entries = []
        for member in self.archive.getmembers():
            if not member.isfile():
                raise ValueError('安装包包含不支持的文件类型。')
            entry = zipfile.ZipInfo(member.name)
            entry.file_size = member.size
            entries.append(entry)
        return entries

    def open(self, entry):
        return self.archive.extractfile(entry.filename)


def open_payload(path):
    return SolidPayload(path) if Path(path).suffix == '.xz' else zipfile.ZipFile(path)
