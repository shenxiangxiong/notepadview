"""Bounded, Unicode-aware regular expression operations without editor state."""
from contextlib import contextmanager
import re
from time import monotonic

import regex
from regex._main import _compile_replacement_helper


REGEX_TIMEOUT = 0.25
MAX_PATTERN_LENGTH = 4096
MAX_TEMPLATE_LENGTH = 64 * 1024


class SearchError(ValueError):
    """A search error that can be shown directly to the user."""


@contextmanager
def _search_errors(message):
    try:
        yield
    except SearchError:
        raise
    except TimeoutError:
        raise SearchError('正则操作超时，请简化表达式后重试。') from None
    except (re.error, regex.error, IndexError, KeyError, ValueError, OverflowError, RecursionError):
        raise SearchError(message) from None


class BoundedSearch:
    def __init__(self, term, regular_expression=True, replacement=None,
                 case_sensitive=False, whole_word=False):
        self.deadline = monotonic() + REGEX_TIMEOUT
        self.regular_expression = regular_expression
        if regular_expression and len(term) > MAX_PATTERN_LENGTH:
            raise SearchError('正则表达式过长，请缩短后重试。')
        with _search_errors('正则表达式无效或嵌套过深，请检查后重试。'):
            if regular_expression:
                # 标准库仅校验语法；实际匹配始终由支持超时的引擎执行。
                re.compile(term, re.IGNORECASE | re.MULTILINE)
                self.remaining()
            flags = (0 if case_sensitive else regex.IGNORECASE) | (regex.MULTILINE if regular_expression else 0)
            pattern = term if regular_expression else regex.escape(term)
            if whole_word:
                pattern = r'(?<!\w)(?:' + pattern + r')(?!\w)'
            self.pattern = regex.compile(pattern, flags)
            self.remaining()
        if replacement is not None and regular_expression:
            if len(replacement) > MAX_TEMPLATE_LENGTH:
                raise SearchError('替换内容过长，请缩短后重试。')
            with _search_errors('替换内容无效，请检查转义字符和捕获组引用。'):
                # 固定版本引擎的模板编译器可在没有匹配时提前验证替换内容。
                parts = _compile_replacement_helper(self.pattern, replacement)
                if any(isinstance(part, int) and part > self.pattern.groups for part in parts):
                    raise SearchError('替换内容引用了不存在的捕获组，请检查后重试。')
                self.remaining()

    def remaining(self):
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise SearchError('正则操作超时，请简化表达式后重试。')
        return remaining

    def find(self, text, start, end, backward=False, skip_empty=False):
        with _search_errors('正则表达式无法完成查找，请简化后重试。'):
            if backward:
                previous = last = None
                # 正向枚举保留完整上下文；所有迭代共用同一截止时间。
                for match in self.pattern.finditer(text, timeout=self.remaining()):
                    self.remaining()
                    last = match
                    if match.end() <= start and not (skip_empty and match.span() == (start, start)):
                        previous = match
                    if previous is not None and match.end() >= start:
                        break
                self.remaining()
                return previous if previous is not None else last
            match = None
            if skip_empty:
                # 只跳过已消费的空匹配，同位置的非空分支仍应被访问。
                for candidate in self.pattern.finditer(text, end, timeout=self.remaining()):
                    self.remaining()
                    if candidate.span() != (end, end):
                        match = candidate
                        break
            else:
                match = self.pattern.search(text, end, timeout=self.remaining())
            if match is None:
                match = self.pattern.search(text, 0, timeout=self.remaining())
            self.remaining()
            return match

    def count(self, text):
        with _search_errors('正则表达式无法完成查找，请简化后重试。'):
            count = 0
            for _ in self.pattern.finditer(text, timeout=self.remaining()):
                self.remaining()
                count += 1
            self.remaining()
            return count

    def selected_match(self, text, start, end):
        with _search_errors('正则表达式无法完成查找，请简化后重试。'):
            match = self.pattern.match(text, start, timeout=self.remaining())
            if match is not None and match.span() != (start, end):
                # 终点断言允许回溯到选区边界，同时保留边界外的环视上下文。
                term = self.pattern.pattern
                if self.pattern.flags & regex.VERBOSE:
                    term += '\n'
                exact = regex.compile('(?:' + term + r')(?=[\s\S]{'
                                      + str(len(text) - end) + r'}\Z)', self.pattern.flags)
                match = exact.match(text, start, timeout=self.remaining())
            self.remaining()
            return match if match is not None and match.span() == (start, end) else None

    def expand(self, match, replacement):
        with _search_errors('替换内容无效，请检查转义字符和捕获组引用。'):
            self.remaining()
            result = match.expand(replacement) if self.regular_expression else replacement
            self.remaining()
            return result

    def replace_all(self, text, replacement):
        with _search_errors('替换内容无效，请检查转义字符和捕获组引用。'):
            value = replacement if self.regular_expression else lambda _: replacement
            result = self.pattern.subn(value, text, timeout=self.remaining())
            self.remaining()
            return result
