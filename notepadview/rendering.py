from html import escape

from markdown_it import MarkdownIt
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

from notepadview.palettes import theme_colors
from notepadview.syntax import style_for_theme


MAX_PREVIEW_HIGHLIGHT_BYTES = 256 * 1024


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
        chunks.append(f'<pre class="preview-chunk" style="contain-intrinsic-size:auto {estimated_height}px">'
                      '<code>' + escape(text) + '</code></pre>')
    return '<div class="large-preview">' + ''.join(chunks) + '</div>'


def parse_markdown(source, highlight_code=None):
    parser = MarkdownIt('commonmark', {'html': False, 'highlight': highlight_code})
    parser.enable('table').enable('strikethrough').use(tasklists_plugin)
    tokens = parser.parse(source)
    for token in tokens:
        if token.type == 'heading_open' and token.map:
            token.attrSet('id', f'heading-{token.map[0]}')
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
    large = len(source.encode('utf-8')) > MAX_PREVIEW_HIGHLIGHT_BYTES
    if large:
        body = large_text_preview(source)
    else:
        try:
            from notepadview.text_types import lexer_for
            lexer = lexer_for(language)
            body = highlight(source, lexer, formatter)
        except ClassNotFound:
            body = escape(source)
    content = body if large else '<pre class="highlight"><code>' + body + '</code></pre>'
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';">
<style>html {{ background:{bg}; color:{fg}; }} body {{ margin:0; padding:16px 20px 40px; }}
pre {{ margin:0; white-space:pre-wrap; overflow-wrap:anywhere; border:0; padding:0; background:transparent; }}
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
               else '<pre>' + escape(source) + '</pre>')
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
