from html import escape
import re
from markdown_it import MarkdownIt
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

from notepadview.palettes import theme_colors
from notepadview.syntax import style_for_theme


MAX_PREVIEW_HIGHLIGHT_BYTES = 256 * 1024


COMPACT_HIGHLIGHT_SCRIPT = r'''
(() => {
    globalThis.nvSyntaxObserver?.disconnect();
    for (const key of globalThis.nvSyntaxKeys || []) globalThis.CSS?.highlights?.delete(key);
    if (!document.documentElement.dataset.codePreview?.startsWith('json:') ||
            !globalThis.CSS?.highlights || !globalThis.StaticRange) {
        delete globalThis.nvPrepareCode;
        return;
    }
    const previewKey = document.documentElement.dataset.codePreview;
    if (globalThis.nvSyntaxStyleKey !== previewKey) {
        globalThis.nvSyntaxStyles = new Map();
        globalThis.nvSyntaxStyleKey = previewKey;
    }
    const styles = globalThis.nvSyntaxStyles, visible = new Set();
    for (const child of document.querySelectorAll('pre.highlight span')) {
        const key = child.className;
        if (!styles.has(key)) {
            const style = getComputedStyle(child);
            styles.set(key, {color:style.color, weight:style.fontWeight,
                             fontStyle:style.fontStyle});
        }
    }
    globalThis.nvPrepareCode = root => {
      for (const code of root.querySelectorAll('pre.highlight code')) {
        if (!code.nvTokens) {
            let offset = 0;
            code.nvTokens = [];
            for (const child of code.childNodes) {
                const end = offset + child.textContent.length;
                if (child.nodeType === Node.ELEMENT_NODE) {
                    const key = child.className;
                    if (!styles.has(key)) {
                        const probe = document.createElement('pre');
                        probe.className = 'highlight';
                        probe.style.cssText = 'position:absolute;visibility:hidden';
                        const probeCode = document.createElement('code');
                        const sample = child.cloneNode(false);
                        sample.textContent = 'x';
                        probeCode.append(sample); probe.append(probeCode);
                        document.body.append(probe);
                        const style = getComputedStyle(sample);
                        styles.set(key, {color:style.color, weight:style.fontWeight,
                                         fontStyle:style.fontStyle});
                        probe.remove();
                    }
                    code.nvTokens.push([key, offset, end]);
                }
                offset = end;
            }
            code.parentElement.nvOriginalHTML = code.parentElement.innerHTML;
            code.textContent = code.textContent;
        }
      }
    };
    globalThis.nvPrepareCode(document);
    function paint() {
        const groups = new Map();
        for (const code of visible) {
            const rect = code.parentElement.getBoundingClientRect();
            if (rect.bottom < -800 || rect.top > innerHeight + 800) continue;
            for (const [key, startOffset, endOffset] of code.nvTokens) {
                if (!groups.has(key)) groups.set(key, []);
                const range = document.createRange();
                range.setStart(code.firstChild, startOffset);
                range.setEnd(code.firstChild, endOffset);
                groups.get(key).push(range);
            }
        }
        for (const key of globalThis.nvSyntaxKeys || []) CSS.highlights.delete(key);
        globalThis.nvSyntaxKeys = [...groups.keys()];
        for (const [key, ranges] of groups) CSS.highlights.set(key, new Highlight(...ranges));
    }
    const observer = new IntersectionObserver(entries => {
        let changed = false;
        for (const entry of entries) {
            const code = entry.target.firstElementChild;
            if (entry.isIntersecting && !visible.has(code)) {
                visible.add(code); changed = true;
            } else if (!entry.isIntersecting && visible.delete(code)) changed = true;
        }
        if (changed) paint();
    }, {rootMargin:'800px'});
    globalThis.nvSyntaxObserver = observer;
    for (const code of document.querySelectorAll('pre.highlight code')) observer.observe(code.parentElement);
    paint();
    let sheet = document.getElementById('nv-syntax-colors');
    if (!sheet) {
        sheet = document.createElement('style');
        sheet.id = 'nv-syntax-colors'; document.head.append(sheet);
    }
    sheet.textContent = [...styles].map(([key, style]) =>
        '::highlight(' + key + '){color:' + style.color + '}').join('\n');
})()
'''


def preview_colors(theme):
    colors = dict(theme_colors(theme))
    if theme == 'nv':
        colors.update(bg=colors['preview_frame'])
    return colors


def preview_paper_css(theme):
    if theme != 'nv':
        return ''
    colors = preview_colors(theme)
    return f'''html {{ background:{colors['bg']}; min-height:100%; }}
body {{ background:{colors['bg']}; color:{colors['fg']}; min-height:100vh;
box-sizing:border-box; }}'''


def preview_scrollbar_css(theme):
    colors = theme_colors(theme)
    return f'''
::-webkit-scrollbar {{ width:10px; height:10px; background:{colors['pane']}; }}
::-webkit-scrollbar-track {{ background:{colors['pane']}; border-radius:4px; }}
::-webkit-scrollbar-thumb {{ background:{colors['border']}; border:2px solid {colors['pane']};
    border-radius:4px; min-height:26px; min-width:26px; background-clip:padding-box; }}
::-webkit-scrollbar-button {{ display:none; width:0; height:0; }}
::-webkit-scrollbar-corner {{ background:{colors['pane']}; }}
'''


def large_text_preview(source):
    lines = source.splitlines(keepends=True)
    chunks = []
    for start in range(0, len(lines), 200):
        text = ''.join(lines[start:start + 200])
        estimated_height = max(1, len(lines[start:start + 200])) * 26
        chunks.append(f'<pre data-source-line="{start}" data-source-text="true" class="preview-chunk" style="contain-intrinsic-size:auto {estimated_height}px">'
                      '<code>' + escape(text) + '</code></pre>')
    return '<div class="large-preview">' + ''.join(chunks) + '</div>'


def _markdown_color(state, silent):
    tail = state.src[state.pos:]
    if tail.startswith('</span>') and getattr(state, '_color_depth', 0):
        if not silent:
            state.push('html_inline', '', 0).content = '</span>'
            state._color_depth -= 1
        state.pos += len('</span>')
        return True
    match = re.match(r'<span style="color:(#[0-9a-fA-F]{6})">', tail)
    if not match:
        return False
    depth = 1
    for tag in re.finditer(r'<span\b[^>]*>|</span>', tail[match.end():]):
        depth += -1 if tag[0] == '</span>' else 1
        if depth == 0:
            break
    if depth:
        return False
    if not silent:
        state.push('html_inline', '', 0).content = match[0]
        state._color_depth = getattr(state, '_color_depth', 0) + 1
    state.pos += match.end()
    return True


def parse_markdown(source, highlight_code=None):
    parser = MarkdownIt('commonmark', {'html': False, 'highlight': highlight_code})
    parser.inline.ruler.before('html_inline', 'font_color', _markdown_color)
    parser.enable('table').enable('strikethrough').use(tasklists_plugin)
    tokens = parser.parse(source)
    for token in tokens:
        if token.map and token.nesting != -1:
            token.attrSet('data-source-line', str(token.map[0]))
            token.attrSet('data-source-end', str(token.map[1]))
        if token.type == 'heading_open' and token.map:
            token.attrSet('id', f'heading-{token.map[0]}')
    for name in ('fence', 'code_block'):
        original = parser.renderer.rules[name]

        def mapped_code(tokens, index, options, env, render=original):
            html = render(tokens, index, options, env)
            token = tokens[index]
            if token.map:
                html = html.replace('<pre', '<pre' + parser.renderer.renderAttrs(token), 1)
            return html

        parser.renderer.rules[name] = mapped_code
    return parser, tokens


def markdown_headings(source):
    _, tokens = parse_markdown(source)
    headings = []
    for index, token in enumerate(tokens):
        if token.type == 'heading_open' and token.map:
            inline = tokens[index + 1]
            title = ''.join(' ' if child.type in ('softbreak', 'hardbreak') else child.content
                            for child in inline.children or []
                            if child.type in ('text', 'code_inline', 'image', 'softbreak', 'hardbreak'))
            headings.append((token.map[0], int(token.tag[1:]), title))
    return headings


def render_code_preview(source: str, language: str, theme: str = 'light') -> str:
    colors = preview_colors(theme)
    bg, fg = (colors[key] for key in ('bg', 'fg'))
    formatter = HtmlFormatter(style=style_for_theme(theme), nowrap=True, nobackground=True)
    try:
        from notepadview.text_types import lexer_for
        lexer = lexer_for(language, stripnl=False, ensurenl=False)
        body = highlight(source, lexer, formatter)
        if not source.endswith(('\n', '\r')) and body.endswith('\n'):
            body = body[:-1]
    except ClassNotFound:
        body = escape(source)
    lines = body.splitlines(keepends=True)
    from zlib import crc32
    blocks = []
    start = 0
    # Content-defined boundaries keep later blocks stable when lines are inserted or deleted.
    for end, line in enumerate(lines, 1):
        if end - start >= 100 or crc32(line.encode('utf-8')) % 32 == 0 or end == len(lines):
            block = ''.join(lines[start:end])
            blocks.append(f'<pre data-source-line="{start}" data-source-text="true" class="highlight">'
                          '<code>' + block + '</code></pre>')
            start = end
    content = ''.join(blocks) if blocks else '<pre data-source-line="0" data-source-text="true" class="highlight"><code></code></pre>'
    return f'''<!doctype html><html lang="zh-CN" data-code-preview="{escape(language, quote=True)}:{escape(theme, quote=True)}"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';">
<style>html {{ background:{bg}; color:{fg}; }} body {{ margin:0; padding:16px 20px 40px; }}
pre {{ margin:0; white-space:pre-wrap; overflow-wrap:anywhere; border:0; padding:0; background:transparent; }}
pre.highlight {{ content-visibility:auto; contain-intrinsic-size:auto 800px; }}
code {{ font:14px/1.8 'Cascadia Code',Consolas,monospace; }}
.large-preview {{ border:0; padding:0; background:transparent; }}
.large-preview .preview-chunk {{ content-visibility:auto; border:0; padding:0; }}
{preview_scrollbar_css(theme)}
{preview_paper_css(theme)}
{formatter.get_style_defs('.highlight')}
</style></head><body>{content}</body></html>'''


def render_plain_text(source: str, theme: str = 'light') -> str:
    dark = theme in ('dark', 'nv')
    colors = preview_colors(theme)
    bg, fg, border = (colors[key] for key in ('bg', 'fg', 'border'))
    content = (large_text_preview(source) if len(source.encode('utf-8')) > MAX_PREVIEW_HIGHLIGHT_BYTES
               else '<pre data-source-line="0" data-source-text="true">' + escape(source) + '</pre>')
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Notepad View</title><style>
html {{ color-scheme:{'dark' if dark else 'light'}; background:{bg}; color:{fg}; }}
body {{ margin:0 auto; padding:36px 40px 80px; max-width:960px; font:16px/1.85 'Microsoft YaHei UI','Segoe UI',sans-serif; }}
pre {{ margin:0; border:0; white-space:pre-wrap; overflow-wrap:anywhere; font:inherit; }}
.large-preview .preview-chunk {{ content-visibility:auto; border:0; }}
{preview_scrollbar_css(theme)}
{preview_paper_css(theme)}
@media(max-width:600px) {{ body {{ padding:24px 20px 60px; }} }}
</style></head><body>{content}</body></html>'''


def render_html(source: str, theme: str = 'light', base_url: str = '') -> str:
    dark = theme in ('dark', 'nv')
    formatter = HtmlFormatter(style=style_for_theme(theme), nowrap=True, nobackground=True)

    def highlight_code(code, language, _attributes):
        if len(code.encode('utf-8')) > MAX_PREVIEW_HIGHLIGHT_BYTES:
            return '<pre class="highlight"><code>' + escape(code) + '</code></pre>'
        try:
            lexer = get_lexer_by_name(language or 'text')
        except ClassNotFound:
            return '<pre class="highlight"><code>' + escape(code) + '</code></pre>'
        return '<pre class="highlight"><code>' + highlight(code, lexer, formatter) + '</code></pre>'

    parser, tokens = parse_markdown(source, highlight_code)
    body = parser.renderer.render(tokens, parser.options, {})
    colors = preview_colors(theme)
    bg, fg, muted, border, code, accent = (colors[key] for key in
        ('bg', 'fg', 'muted', 'border', 'code', 'accent'))
    base = '<base href="' + escape(base_url, quote=True) + '">' if base_url else ''
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src file: data: https: http:; style-src 'unsafe-inline'; font-src 'none';">
{base}<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Notepad View</title><style>
:root {{ color-scheme: {'dark' if dark else 'light'}; }}
* {{ box-sizing: border-box; }}
html {{ background:{bg}; color:{fg}; }}
body {{ margin:0 auto; padding:36px 40px 80px; max-width:960px; font:16px/1.85 'Microsoft YaHei UI','Segoe UI',sans-serif; overflow-wrap:anywhere; }}
h1,h2,h3,h4 {{ line-height:1.45; font-weight:650; margin:1.5em 0 .6em; }}
h1 {{ font-size:30px; margin-top:0; padding-bottom:16px; border-bottom:1px solid {border}; }}
h2 {{ font-size:23px; }} h3 {{ font-size:19px; }}
p {{ margin:0 0 1em; }} a {{ color:{accent}; text-decoration:none; }}
blockquote {{ border-left:3px solid {accent}; margin:20px 0; padding:6px 20px; color:{muted}; background:{code}; }}
blockquote p:last-child {{ margin:0; }}
code {{ font:14px/1.75 'Cascadia Code',Consolas,monospace; background:{code}; padding:2px 5px; border-radius:3px; }}
pre {{ padding:18px; border:1px solid {border}; border-radius:5px; overflow-x:auto; background:{code}; }}
pre code {{ padding:0; background:transparent; }} .highlight {{ background:{code}; }}
table {{ border-collapse:collapse; width:100%; margin:20px 0; }}
th,td {{ border:1px solid {border}; padding:9px 14px; text-align:left; }} th {{ background:{code}; }}
img {{ max-width:100%; height:auto; border-radius:4px; }}
hr {{ border:0; border-top:1px solid {border}; margin:28px 0; }}
li {{ margin:4px 0; }} .task-list-item {{ list-style:none; }} input {{ accent-color:{accent}; }}
{preview_scrollbar_css(theme)}
{preview_paper_css(theme)}
{formatter.get_style_defs('.highlight')}
@media(max-width:600px) {{ body {{ padding:24px 20px 60px; }} }}
</style></head><body>{body}</body></html>'''
