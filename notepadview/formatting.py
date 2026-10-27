"""Pure document formatters; parsing errors never alter the source document."""
import json
import re
from xml.dom import minidom
from xml.parsers.expat import ExpatError

import sqlparse
from sqlparse import tokens as sql_tokens
from sqlparse.exceptions import SQLParseError

from notepadview.errors import chinese_error


class JsonNumber(str):
    pass


class JsonObject(list):
    pass


class _SqlFormattingError(ValueError, SQLParseError):
    pass


def format_json(source: str) -> str:
    def reject_constant(value):
        raise ValueError('JSON 不支持非标准数值常量：' + value)
    try:
        value = json.loads(source, parse_int=JsonNumber, parse_float=JsonNumber,
                           object_pairs_hook=JsonObject, parse_constant=reject_constant)
    except json.JSONDecodeError as error:
        localized = json.JSONDecodeError(error.msg, error.doc, error.pos)
        # 保留解析器类型及位置属性，仅替换面向用户的字符串。
        localized.args = (chinese_error(error),)
        raise localized from error
    # Keep the parser's number lexemes and ordered pairs, not binary floats or dicts.
    def serialize(item, depth=0):
        if isinstance(item, JsonNumber):
            return str(item)
        if isinstance(item, (JsonObject, list)):
            is_object = isinstance(item, JsonObject)
            opening, closing = ('{', '}') if is_object else ('[', ']')
            if not item:
                return opening + closing
            indent = '  ' * (depth + 1)
            entries = ([json.dumps(k, ensure_ascii=False) + ': ' + serialize(v, depth + 1)
                        for k, v in item] if is_object else [serialize(v, depth + 1) for v in item])
            return opening + '\n' + ',\n'.join(indent + entry for entry in entries) + '\n' + '  ' * depth + closing
        return json.dumps(item, ensure_ascii=False, allow_nan=False)
    return serialize(value)


def compact_json(source: str) -> str:
    formatted = format_json(source)
    return re.sub(r'"(?:\\.|[^"\\])*"|\s+',
                  lambda match: match[0] if match[0].startswith('"') else '', formatted)


def format_jsonl(source: str) -> str:
    lines = source.splitlines(keepends=True)
    result = []
    for number, line in enumerate(lines, 1):
        ending = '\r\n' if line.endswith('\r\n') else '\n' if line.endswith('\n') else '\r' if line.endswith('\r') else ''
        content = line[:-len(ending)] if ending else line
        if not content.strip():
            result.append(line)
            continue
        try:
            result.append(compact_json(content) + ending)
        except ValueError as error:
            raise ValueError(f'JSONL 第 {number} 行不是完整 JSON：{error}') from error
    return ''.join(result)


def _repair_escaped_json(source: str) -> str | None:
    parts = []
    position = 0
    found = False
    while position < len(source):
        if source.startswith(('//', '/*'), position):
            if source.startswith('//', position):
                terminator = re.search(r'[\r\n]', source[position:])
                end = len(source) if terminator is None else position + terminator.start()
            else:
                closing = source.find('*/', position + 2)
                end = len(source) if closing < 0 else closing + 2
            parts.append(source[position:end])
            position = end
        elif source[position] in ('"', "'"):
            quote = source[position]
            end = position + 1
            while end < len(source):
                if source[end] == '\\':
                    end += 2
                elif source[end] == quote:
                    end += 1
                    break
                else:
                    end += 1
            parts.append(source[position:end])
            position = end
        elif source.startswith('\\"', position):
            found = True
            # 局部修复仅接受无内部转义、成对转义且后接冒号的属性名。
            match = re.match(r'\\"([^"\\]*)\\"(?=[ \t\r\n]*:)', source[position:])
            if match is None:
                parts.append(source[position:])
                break
            parts.append('"' + match[1] + '"')
            position += match.end()
        else:
            parts.append(source[position])
            position += 1
    if not found:
        return None
    candidates = set()
    try:
        candidates.add(format_json(''.join(parts)))
    except ValueError:
        pass
    decoded = source
    for _ in range(8):
        try:
            # 逐层按 JSON 字符串规则解码，解析成功即停止，保留内容中的合法转义。
            decoded = json.loads('"' + decoded + '"')
        except ValueError:
            break
        if not decoded.lstrip().startswith(('{', '[')):
            break
        try:
            candidates.add(format_json(decoded))
            break
        except json.JSONDecodeError as error:
            # 仅允许完整顶层对象后恰好多出一个 }；其他尾随内容仍拒绝。
            if (error.msg == 'Extra data' and decoded.lstrip().startswith('{')
                    and decoded[error.pos:].strip() == '}'):
                try:
                    candidates.add(format_json(decoded[:error.pos]))
                    break
                except ValueError:
                    pass
        except ValueError:
            break
    if len(candidates) != 1:
        raise ValueError('JSON 结构引号转义无法唯一确认，保留原文。')
    return candidates.pop()


def repair_json(source: str) -> str:
    """修复常见语法问题，或合并空白分隔的完整对象/数组记录。"""
    escaped = _repair_escaped_json(source)
    if escaped is not None:
        return escaped
    parts = []
    position = 0
    while position < len(source):
        char = source[position]
        if char in ('"', "'"):
            start = position
            quote = char
            position += 1
            while position < len(source):
                if source[position] == '\\':
                    position += 2
                elif source[position] == quote:
                    position += 1
                    break
                else:
                    position += 1
            else:
                raise ValueError('JSON 字符串不完整，无法保守修复。')
            token = source[start:position]
            if quote == "'":
                escapes = re.findall(r'\\(.)', token, flags=re.DOTALL)
                if any(escape not in "\"'\\/bfnrtu" for escape in escapes):
                    raise ValueError('JSON 含有不支持的转义，无法安全转换。')
                content = re.sub(r'\\.|"', lambda m: "'" if m[0] == "\\'" else
                                 ('\\"' if m[0] == '"' else m[0]), token[1:-1])
                try:
                    value = json.loads('"' + content + '"')
                except ValueError as error:
                    raise ValueError('JSON 单引号字符串无法安全转换。') from error
                token = json.dumps(value, ensure_ascii=False)
            parts.append(token)
        elif source.startswith('//', position):
            terminator = re.search(r'[\r\n]', source[position:])
            position = len(source) if terminator is None else position + terminator.start()
            parts.append(' ')
        elif source.startswith('/*', position):
            end = source.find('*/', position + 2)
            if end < 0:
                raise ValueError('JSON 注释不完整，无法保守修复。')
            parts.append(' ' + '\n' * source[position:end + 2].count('\n'))
            position = end + 2
        else:
            parts.append(char)
            position += 1
    candidate = ''.join(parts)
    candidate = re.sub(r'"(?:\\.|[^"\\])*"|,\s*(?=[}\]])',
                       lambda match: match[0] if match[0].startswith('"') else '', candidate)
    try:
        return format_json(candidate)
    except json.JSONDecodeError as error:
        if error.msg != 'Extra data':
            raise
        # 只解析原文来确定边界；输出保留原始记录，避免重编码字符串和数值。
        def reject_constant(value):
            raise ValueError('JSON 不支持非标准数值常量：' + value)
        decoder = json.JSONDecoder(parse_int=JsonNumber, parse_float=JsonNumber,
                                   object_pairs_hook=JsonObject, parse_constant=reject_constant)
        records = []
        position = 0
        try:
            while position < len(source):
                start = re.compile(r'[ \t\r\n]*').match(source, position).end()
                if start == len(source):
                    break
                if records and start == position:
                    raise ValueError('JSON 记录之间缺少空白分隔。')
                if source[start] not in '{[':
                    raise ValueError('JSON 多条记录必须是对象或数组。')
                _, position = decoder.raw_decode(source, start)
                records.append(source[start:position])
            if len(records) < 2:
                raise ValueError('JSON 多条记录必须至少包含两条完整记录。')
        except ValueError:
            raise error
        return '[\n' + ',\n'.join(records) + '\n]'


def repair_xml(source: str) -> str:
    """保留结构，只修复裸 & 和明确的无引号属性值。"""
    if '<!DOCTYPE' in source.upper() or '<!ENTITY' in source.upper():
        raise ValueError('XML 不支持 DTD 或实体声明。')
    token_pattern = r'<!--[\s\S]*?-->|<!\[CDATA\[[\s\S]*?\]\]>|<\?[\s\S]*?\?>|<(?:"[^"]*"|\'[^\']*\'|[^\'">])*>'
    def escape_ampersands(text):
        return re.sub(r'&(?!#\d+;|#x[0-9a-fA-F]+;|[A-Za-z_:][\w.:-]*;)', '&amp;', text)
    result = []
    end = 0
    for match in re.finditer(token_pattern, source):
        result.append(escape_ampersands(source[end:match.start()]))
        token = match[0]
        if not token.startswith(('<!--', '<![CDATA[', '<?')):
            def quote_attribute(match):
                if match[1] is None:
                    return match[0]
                if match[2].endswith('/'):
                    raise ValueError('XML 无引号属性末尾 / 含义不明确，无法保守修复。')
                return match[1] + '"' + match[2] + '"'
            token = re.sub(r'"[^"]*"|\'[^\']*\'|([\w:.-]+\s*=\s*)([^\s\'"=<>`]+)',
                           quote_attribute, token)
            token = escape_ampersands(token)
        result.append(token)
        end = match.end()
    result.append(escape_ampersands(source[end:]))
    candidate = ''.join(result)
    format_xml(candidate)
    return candidate


def markdown_format(source: str, operation: str, newline: str = '\n') -> str:
    if operation in ('bold', 'italic', 'code'):
        if operation == 'code':
            longest = max((len(m[0]) for m in re.finditer(r'`+', source)), default=0)
            fence = '`' * max(3, longest + 1)
            return fence + newline + source + ('' if source.endswith(('\n', '\r')) else newline) + fence
        marker = '**' if operation == 'bold' else '*'
        return marker + source + marker
    prefix = {'paragraph': '', 'list': '- ', 'quote': '> '}.get(operation)
    if operation.startswith('heading'):
        prefix = '#' * int(operation[-1]) + ' '
    if prefix is None:
        raise ValueError('不支持的 Markdown 操作。')
    lines = source.splitlines(keepends=True)
    return ''.join(prefix + re.sub(r'^(?:#{1,6}\s+|>\s?|[-+*]\s+|\d+[.)]\s+)', '', line)
                   for line in lines) if lines else prefix.rstrip()


def format_xml(source: str) -> str:
    if '<!DOCTYPE' in source.upper() or '<!ENTITY' in source.upper():
        raise ValueError('XML 不支持 DTD 或实体声明。')
    try:
        document = minidom.parseString(source)
    except ExpatError as error:
        raise ValueError(chinese_error(error)) from error
    try:
        # Preserve mixed content and xml:space instead of inserting whitespace into it.
        def clean(node, preserve=False):
            preserve = preserve or (node.nodeType == node.ELEMENT_NODE and
                                    node.getAttribute('xml:space') == 'preserve')
            text = any(child.nodeType in (child.TEXT_NODE, child.CDATA_SECTION_NODE)
                       and child.data.strip() for child in node.childNodes)
            if preserve or text:
                return
            if not any(c.nodeType == c.ELEMENT_NODE for c in node.childNodes):
                return
            for child in list(node.childNodes):
                if child.nodeType == child.TEXT_NODE and not child.data.strip():
                    node.removeChild(child)
                else:
                    clean(child, preserve)
        clean(document)
        def serialize(node, depth=0):
            indent = '  ' * depth
            if node.nodeType != node.ELEMENT_NODE:
                return indent + node.toxml()
            mixed = node.getAttribute('xml:space') == 'preserve' or any(
                c.nodeType in (c.TEXT_NODE, c.CDATA_SECTION_NODE) and c.data.strip()
                for c in node.childNodes)
            leaf_text = node.childNodes and all(c.nodeType in (c.TEXT_NODE, c.CDATA_SECTION_NODE) for c in node.childNodes)
            if mixed or leaf_text or not node.childNodes:
                return indent + node.toxml()
            opening = node.cloneNode(False).toxml()[:-2] + '>'
            return indent + opening + '\n' + '\n'.join(serialize(c, depth + 1) for c in node.childNodes) + '\n' + indent + '</' + node.tagName + '>'
        result = '\n'.join(serialize(c) for c in document.childNodes)
        if document.version:
            standalone = '' if document.standalone is None else (' standalone="yes"' if document.standalone else ' standalone="no"')
            encoding = f' encoding="{document.encoding}"' if document.encoding else ''
            result = f'<?xml version="{document.version}"{encoding}{standalone}?>\n' + result
        return result
    finally:
        document.unlink()


def format_sql(source: str) -> str:
    # PostgreSQL string concatenation requires the newline between adjacent literals.
    previous = None
    separator = ''
    try:
        for statement in sqlparse.parse(source):
            for token in statement.flatten():
                if token.is_whitespace:
                    separator += token.value
                    continue
                if (previous is not None and previous.ttype in sql_tokens.Literal.String.Single
                        and token.ttype in sql_tokens.Literal.String.Single
                        and ('\n' in separator or '\r' in separator)):
                    return sqlparse.format(source, keyword_case='upper')
                previous, separator = token, ''
        return sqlparse.format(source, keyword_case='upper', reindent=True,
                               indent_width=4, use_space_around_operators=True)
    except SQLParseError as error:
        raise _SqlFormattingError(chinese_error(error)) from error
