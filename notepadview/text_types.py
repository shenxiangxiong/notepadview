"""文本类型注册、有限样本识别及离线词法器。识别不执行源代码。"""
from dataclasses import dataclass
from pathlib import Path
import csv
import io
import json
import re

from pygments.lexers import get_lexer_by_name
from pygments.lexer import RegexLexer
from pygments.token import Text, String, Number, Punctuation
from pygments.util import ClassNotFound


class DelimitedLexer(RegexLexer):
    tokens = {'root': [(r'"(?:[^"]|"")*"', String),
                       (r'[,\t]', Punctuation), (r'\r\n|\r|\n', Text),
                       (r'-?\d+(?:\.\d+)?', Number), (r'[^",\t\r\n]+', String),
                       (r'.', Text)]}


@dataclass(frozen=True)
class TextType:
    label: str
    lexer: str
    suffixes: tuple[str, ...]
    format_enabled: bool = False


TEXT_TYPES = {
    'md': TextType('Markdown', 'markdown', ('md', 'markdown', 'mdown', 'mkd')),
    'txt': TextType('纯文本 / 日志', 'text', ('txt', 'log', 'text')),
    'json': TextType('JSON', 'json', ('json',), True),
    'jsonl': TextType('JSONL', 'json', ('jsonl', 'ndjson'), True),
    'yaml': TextType('YAML', 'yaml', ('yaml', 'yml')),
    'xml': TextType('XML', 'xml', ('xml', 'xsd', 'xsl'), True),
    'html': TextType('HTML', 'html', ('html', 'htm')),
    'css': TextType('CSS', 'css', ('css',)),
    'scss': TextType('SCSS', 'scss', ('scss',)),
    'js': TextType('JavaScript', 'javascript', ('js', 'mjs', 'cjs')),
    'ts': TextType('TypeScript', 'typescript', ('ts', 'tsx')),
    'sql': TextType('SQL', 'sql', ('sql',), True),
    'python': TextType('Python', 'python', ('py', 'pyw')),
    'powershell': TextType('PowerShell', 'powershell', ('ps1',)),
    'ini': TextType('INI', 'ini', ('ini', 'cfg', 'conf')),
    'toml': TextType('TOML', 'toml', ('toml',)),
    'csv': TextType('CSV', 'text', ('csv',)),
    'tsv': TextType('TSV', 'text', ('tsv',)),
}
SAMPLE_LIMIT = 64 * 1024
JSON_ARRAY_LIMIT = 1024 * 1024


def lexer_for(language, **options):
    if language in ('csv', 'tsv'):
        return DelimitedLexer(**options)
    try:
        return get_lexer_by_name(TEXT_TYPES.get(language, TEXT_TYPES['txt']).lexer, **options)
    except ClassNotFound:
        return get_lexer_by_name('text', **options)


def type_for_path(path):
    suffix = Path(path).suffix.casefold().lstrip('.')
    return next((key for key, item in TEXT_TYPES.items() if suffix in item.suffixes), 'txt')


def save_filter(language):
    item = TEXT_TYPES[language]
    return f'{item.label} (*.{item.suffixes[0]})'


def _json_container(source):
    def reject_constant(value):
        raise ValueError('非 JSON 常量：' + value)

    try:
        value = json.loads(source, parse_int=str, parse_float=str, parse_constant=reject_constant)
        return isinstance(value, (dict, list))
    except (ValueError, RecursionError):
        return False


def detect_text_type(path, source):
    fallback = type_for_path(path)
    if fallback != 'txt':
        return fallback
    sample = source[:SAMPLE_LIMIT]
    truncated = len(source) > SAMPLE_LIMIT
    text = sample.lstrip('\ufeff \t\r\n')
    if not text:
        return fallback
    lines = text.splitlines()
    complete_lines = lines[:-1] if truncated else lines
    records = [line for line in complete_lines if line.strip()]
    if len(records) >= 2 and all(_json_container(line) for line in records):
        return 'jsonl'
    if not truncated and _json_container(text):
        return 'json'
    # 大数组只在有界全文严格解析成功时识别，避免将后半段 JS 表达式当作 JSON。
    if (truncated and text.startswith('[') and len(source) <= JSON_ARRAY_LIMIT
            and _json_container(source.lstrip('\ufeff \t\r\n'))):
        return 'json'
    # 先排除明确代码结构，再允许带结构引号的损坏 JSON 进入手动修复。
    outside_strings = re.sub(r'"(?:\\.|[^"\\])*"|\x27(?:\\.|[^\x27\\])*\x27', '""', text)
    if re.search(r'=>|\bfunction\b|\b(?:const|let|var)\s+\w+', outside_strings):
        return 'ts' if fallback == 'ts' else 'js'
    if text.startswith(('{', '[')) and re.search(r':\s*(?!(?:true|false|null)\b)[A-Za-z_$][\w$]*', outside_strings):
        return fallback
    # 结构键必须为带引号的 JSON 键；不能将任意 { 开头的 JS 交给 JSON 修复。
    if re.match(r'\{\s*(?:"(?:[^"\\]|\\.)*"|(?:\\+"[^"\\]+\\+"))\s*:', text):
        return 'json'
    if re.match(r'\[\s*\{\s*"[^"\n]+"\s*:', text):
        return 'json'
    decoded = text
    if not truncated:
        for _ in range(8):
            try:
                decoded = json.loads(decoded) if decoded.startswith('"') else json.loads('"' + decoded + '"')
            except (ValueError, RecursionError):
                break
            if not isinstance(decoded, str):
                break
            if _json_container(decoded) or re.match(r'\{\s*"[^"\n]+"\s*:', decoded):
                return 'json'
    if re.match(r'(?is)(?:<!doctype\s+html\b|<html\b|<(?:head|body)\b)', text):
        return 'html'
    if (re.match(r'<\?xml\b', text) or re.match(r'(?s)<[\w:.-]+(?:\s[^<>]*)?>.*</[\w:.-]+\s*>', text)
            or re.fullmatch(r'<[\w:.-]+(?:\s[^<>]*)?/\s*>\s*', text)):
        return 'xml'
    if re.search(r'(?m)^\s*(?:async\s+)?def\s+\w+\s*\([^\n]*\)\s*(?:->[^\n:]+)?:', text) or text.startswith('#!/usr/bin/env python'):
        return 'python'
    if re.search(r'(?m)^\s*(?:export\s+)?(?:interface\s+\w+\s*\{|type\s+\w+\s*=)', text):
        return 'ts'
    if re.search(r'(?m)^\s*(?:(?:export\s+)?(?:const|let|var)\s+\w+\s*=|(?:export\s+)?function\s+\w+\s*\()', text):
        return 'js' if fallback != 'ts' else 'ts'
    if re.search(r'(?is)^\s*(?:select\b.+\bfrom\b|insert\s+into\b|create\s+table\b|update\s+\w+\s+set\b|delete\s+from\b)', text):
        return 'sql'
    if re.search(r'(?m)^\$[\w-]+\s*:', text) and '{' in text:
        return 'scss'
    if re.search(r'(?m)^[.#]?[\w-]+(?:\s+[\w.#:-]+)*\s*\{\s*[\w-]+\s*:[^{}]+;', text):
        return 'scss' if fallback == 'scss' else 'css'
    if re.search(r'(?m)^#{1,6}\s+\S', text) and re.search(r'(?m)^(?:[-*+]\s+|```|>\s+)', text):
        return 'md'
    assignments = re.findall(r'(?m)^\s*[\w.-]+\s*=\s*(.+)$', text)
    if re.search(r'(?m)^\s*\[[\w."-]+\]\s*$', text) and len(assignments) >= 2:
        if fallback in ('ini', 'toml'):
            return fallback
        return 'toml' if any(value.startswith(('"', "'", '[')) for value in assignments) else 'ini'
    yaml_keys = re.findall(r'(?m)^\s*[\w.-]+:\s*(?:[^\n]*)$', text)
    if len(yaml_keys) >= 2 and (text.startswith('---\n') or re.search(r'(?m)^\s+-\s+\S', text)):
        return 'yaml'
    for delimiter, language in (('\t', 'tsv'), (',', 'csv')):
        if len(records) < 3 or delimiter not in records[0]:
            continue
        try:
            rows = list(csv.reader(io.StringIO('\n'.join(records)), delimiter=delimiter, strict=True))
            width = len(rows[0])
            if width >= 2 and all(len(row) == width for row in rows) and all(re.fullmatch(r'[\w .-]+', cell) for cell in rows[0]):
                return language
        except csv.Error:
            pass
    return fallback
