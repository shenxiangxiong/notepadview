"""将异常转换为可展示的中文提示，原始异常保留用于诊断。"""
import errno
import json
import re
from xml.parsers import expat

from sqlparse.exceptions import SQLParseError


def _is_chinese_message(message: str) -> bool:
    if not re.search(r'[\u3400-\u9fff]', message):
        return False
    # 路径和格式标识可保留，英文异常句子不能混入中文提示。
    text = re.sub(r'(?:[A-Za-z]:[\\/]|/)[^\n，；。]*?(?=:\s|[，；。\n]|$)', '', message)
    text = re.sub(r'(?<![\w.])[\w-]+(?:\.[\w-]+)+', '', text)
    return all(word in {'JSON', 'XML', 'SQL', 'DTD', 'UTF', 'NaN', 'Infinity',
                       'Unicode', 'CDATA'}
               for word in re.findall(r'[A-Za-z]+', text))


def chinese_error(error: Exception) -> str:
    """返回非空中文原因；不直接展示英文异常或推测未知故障原因。"""
    if isinstance(error, json.JSONDecodeError):
        reasons = {
            'Expecting value': '缺少值',
            'Expecting property name enclosed in double quotes': '属性名必须使用双引号',
            "Expecting ':' delimiter": '缺少冒号',
            "Expecting ',' delimiter": '缺少逗号',
            'Extra data': '存在多余内容',
        }
        reason = reasons.get(error.msg, '语法不符合 JSON 规范')
        for prefix, translated in (
            ('Unterminated string', '字符串未结束'),
            ('Invalid \\escape', '无效的转义字符'),
            ('Invalid \\uXXXX escape', '无效的 Unicode 转义序列'),
            ('Invalid control character', '字符串包含无效控制字符'),
            ('Unexpected UTF-8 BOM', '存在不受支持的字节顺序标记'),
        ):
            if error.msg.startswith(prefix):
                reason = translated
                break
        if _is_chinese_message(error.msg):
            reason = error.msg
        return f'JSON 格式错误：{reason}（第 {error.lineno} 行，第 {error.colno} 列）。'
    if isinstance(error, expat.ExpatError):
        reasons = {
            expat.errors.codes[expat.errors.XML_ERROR_TAG_MISMATCH]: '开始与结束标签不匹配',
            expat.errors.codes[expat.errors.XML_ERROR_NO_ELEMENTS]: '缺少根元素或元素未闭合',
            expat.errors.codes[expat.errors.XML_ERROR_UNDEFINED_ENTITY]: '使用了未定义的实体',
            expat.errors.codes[expat.errors.XML_ERROR_DUPLICATE_ATTRIBUTE]: '存在重复属性',
            expat.errors.codes[expat.errors.XML_ERROR_INVALID_TOKEN]: '包含无效字符或标记',
            expat.errors.codes[expat.errors.XML_ERROR_JUNK_AFTER_DOC_ELEMENT]: '根元素后存在多余内容',
            expat.errors.codes[expat.errors.XML_ERROR_UNCLOSED_TOKEN]: '标记未闭合',
            expat.errors.codes[expat.errors.XML_ERROR_UNCLOSED_CDATA_SECTION]: 'CDATA 区段未闭合',
            expat.errors.codes[expat.errors.XML_ERROR_PARTIAL_CHAR]: '字符数据不完整',
            expat.errors.codes[expat.errors.XML_ERROR_UNKNOWN_ENCODING]: '未知的字符编码',
            expat.errors.codes[expat.errors.XML_ERROR_INCORRECT_ENCODING]: '声明的字符编码与内容不一致',
        }
        reason = reasons.get(getattr(error, 'code', None), '语法不符合 XML 规范')
        return f'XML 格式错误：{reason}（第 {error.lineno} 行，第 {error.offset} 列）。'
    if isinstance(error, OSError):
        reasons = {
            errno.ENOENT: '文件或路径不存在', errno.EACCES: '权限不足，无法访问',
            errno.EPERM: '权限不足，无法执行操作', errno.EEXIST: '目标文件或目录已存在',
            errno.ENOSPC: '磁盘空间不足', errno.EIO: '文件读写失败',
            errno.ENOTDIR: '路径不是目录', errno.EISDIR: '目标是目录，无法作为文件操作',
            errno.EROFS: '文件系统为只读', errno.EBUSY: '文件或设备正被占用',
            errno.ENOTEMPTY: '目标目录非空', errno.ENAMETOOLONG: '路径或文件名过长',
            errno.EMFILE: '打开的文件过多', errno.ENFILE: '系统打开的文件过多',
            errno.EINVAL: '文件系统参数或路径无效', errno.ETIMEDOUT: '操作超时',
            errno.EXDEV: '无法跨文件系统移动文件',
        }
        windows_reasons = {
            2: '文件不存在', 3: '路径不存在', 5: '权限不足，无法访问',
            15: '指定的驱动器不存在', 19: '设备处于只读状态',
            21: '设备尚未就绪', 32: '文件正被其他程序占用',
            33: '文件已被锁定', 80: '目标文件已存在', 112: '磁盘空间不足',
            123: '路径或文件名无效', 145: '目标目录非空', 183: '目标文件或目录已存在',
            206: '路径或文件名过长', 267: '目录路径无效',
        }
        reason = windows_reasons.get(getattr(error, 'winerror', None)) or reasons.get(error.errno)
        if reason is None:
            for kind, translated in (
                (FileNotFoundError, '文件或路径不存在'), (PermissionError, '权限不足，无法访问'),
                (FileExistsError, '目标文件或目录已存在'), (IsADirectoryError, '目标是目录'),
                (NotADirectoryError, '路径不是目录'), (TimeoutError, '操作超时'),
            ):
                if isinstance(error, kind):
                    reason = translated
                    break
        reason = reason or '文件系统操作失败，原因尚未确定'
        custom = error.strerror if error.strerror is not None else str(error)
        is_custom = _is_chinese_message(custom)
        if is_custom:
            reason = custom
        paths = [str(path) for path in (error.filename, error.filename2) if path is not None]
        if paths:
            return reason + '\n路径：' + ' → '.join(paths)
        if is_custom:
            return custom
        return reason if reason.endswith(('。', '！', '？')) else reason + '。'
    if isinstance(error, UnicodeDecodeError):
        return f'字符解码失败（字节位置 {error.start}），请检查文件内容并使用 UTF-8 编码保存。'
    if isinstance(error, UnicodeEncodeError):
        return f'字符编码失败（字符位置 {error.start}），当前编码无法表示该字符。'
    if isinstance(error, UnicodeTranslateError):
        return f'字符转换失败（字符位置 {error.start}），请检查字符内容。'
    if isinstance(error, UnicodeError):
        return '字符编码处理失败，请检查文件编码与内容。'
    if isinstance(error, ValueError):
        message = str(error)
        match = re.fullmatch(r'(?:(.+): )?Unsupported workspace schema version (\d+); supported version is (\d+)', message)
        if match:
            path = f'路径：{match[1]}。' if match[1] else ''
            return f'工作区缓存版本 {match[2]} 不受支持，当前仅支持版本 {match[3]}。{path}请使用兼容版本的程序打开。'
        if message.startswith('Cannot recover corrupt workspace cache '):
            details = message.removeprefix('Cannot recover corrupt workspace cache ')
            path, separator, _ = details.partition(': ')
            location = f'路径：{path}。' if separator else ''
            return f'工作区缓存已损坏，无法从备份恢复。{location}请保留缓存文件以便检查。'
        if message.startswith('Invalid workspace '):
            return '工作区缓存结构无效，请检查缓存数据。'
        if message.startswith('Invalid JSON constant'):
            return 'JSON 包含不受支持的常量，仅支持标准 JSON 数值。'
        if message == 'XML declarations with DTD/entities are not supported':
            return 'XML 不支持 DTD 或实体声明。'
        if message == 'Please choose an application subdirectory.':
            return '请选择应用程序的安装子目录。'
        if message.startswith('Invalid archive path: '):
            return '安装包包含无效或不安全的文件路径。'
        if message.startswith('The package does not contain '):
            return '安装包缺少必需的应用程序文件。'
        if message.startswith(('invalid literal for int()', 'could not convert string to float')):
            return '数值格式无效，请检查输入内容。'
        if _is_chinese_message(message):
            return message
        return '输入或数据无效，请检查内容。'
    if isinstance(error, SQLParseError):
        message = str(error)
        if message.startswith(('Maximum grouping depth', 'Maximum recursion depth')):
            return 'SQL 嵌套层数过深，无法格式化。'
        if message.startswith('Maximum number of tokens'):
            return 'SQL 内容过于复杂，超出格式化处理限制。'
        return 'SQL 解析失败，请检查语句内容。'
    for kind, message in (
        (RecursionError, '内容嵌套层数过深，无法继续处理。'),
        (MemoryError, '可用内存不足，请关闭部分文档后重试。'),
        (TypeError, '数据类型不符合要求，请检查输入内容。'),
        (KeyError, '缺少所需的数据字段，请检查数据内容。'),
        (RuntimeError, '程序运行出错，原因尚未确定。请重试或查看日志。'),
    ):
        if isinstance(error, kind):
            return message
    return '发生未知错误，原因尚未确定。请重试或查看日志。'
