"""Markdown -> HTML tailored for Qt's rich-text engine.

mistune does the parsing; the small amount of post-processing below exists
because the target engine has quirks that HTML alone doesn't cover:

  * ``padding`` on block elements is ignored, so fenced code is wrapped into a
    single-cell table to get real inner padding (see theme.content_css);
  * ``<input type=checkbox>`` is not rendered, task markers are glyphs;
  * relative image paths need to become absolute ``file://`` URLs, and
    oversized images are given an explicit width (Qt ignores ``max-width``).
"""

import re
from collections import namedtuple
from html import unescape
from pathlib import Path

import mistune
from PyQt6.QtGui import QImageReader

from theme import IMAGE_MAX, SURFACE

# One heading of the document outline. `anchor` is the name injected into the
# HTML so the table of contents can scroll to it (QTextBrowser.scrollToAnchor).
Heading = namedtuple("Heading", "level label anchor")

_MARKDOWN = mistune.create_markdown(
    escape=False,  # local, offline viewer: raw HTML is harmless and sometimes used
    plugins=(
        "speedup",
        "strikethrough",
        "table",
        "task_lists",
        "footnotes",
        "url",
        "def_list",
        "abbr",
        "mark",
        "superscript",
        "subscript",
    ),
)

_CODE_BLOCK = re.compile(
    r'<pre><code(?: class="([^"]*)")?>(.*?)</code></pre>', re.S
)
_CHECKBOX = re.compile(r"<input[^>]*type=\"checkbox\"[^>]*/>\s?")
_FOOTNOTES = re.compile(r"<section class=\"footnotes\">(.*?)</section>", re.S)
_IMAGE = re.compile(r"<img src=\"([^\"]*)\"([^>]*)>")
_EXTERNAL = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)
_HEADING = re.compile(r"<h([1-6])( [^>]*)?>(.*?)</h\1>", re.S)
_TABLE = re.compile(r"<table>(.*?)</table>", re.S)
_THEAD = re.compile(r"<thead>(.*?)</thead>", re.S)
_TH = re.compile(r"<th([^>]*)>(.*?)</th>", re.S)
_TD = re.compile(r"<td([^>]*)>(.*?)</td>", re.S)
_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")

# Zebra striping for markdown tables. Qt rich text has no :nth-child, so the
# row colour is written into the HTML. Applied to body rows only.
_TABLE_ROW_A = "#1f1f1f"
_TABLE_ROW_B = "#242424"

_TASK_OPEN, _TASK_DONE = '<span class="task-box">&#9744;</span> ', (
    '<span class="task-box-done">&#9745;</span> '
)


def _code_block(lang: str | None, body: str) -> str:
    """Fenced code -> a card laid out with plain blocks.

    It used to be a single-cell table so ``td`` padding would pad the code, but
    Qt's hit-testing inside a multi-line table cell is broken: clicking between
    two code lines jumps to the cell's *last* line, so selecting one word
    selected from the bottom of the block. A plain ``<pre>`` hit-tests
    correctly, and `text-indent` (which Qt honours, unlike `padding`) supplies
    the left inset; the line-height leading acts as the vertical padding.
    """
    label = f"{(lang or '').removeprefix('language-').strip()}"
    caption = f'<p class="code-lang">{label}</p>' if label else ""
    klass = "code-body has-lang" if label else "code-body"
    code = f'<pre class="{klass}">{body}</pre>'
    return caption + code


def _checkbox(match: re.Match) -> str:
    return _TASK_DONE if "checked" in match.group(0) else _TASK_OPEN


def _footnotes(match: re.Match) -> str:
    return (
        '<div class="footnotes">'
        '<p class="footnotes-title">Сноски</p>'
        f"{match.group(1)}</div>"
    )


def _image(match: re.Match, base: Path | None) -> str:
    src, rest = match.group(1), match.group(2)
    if not base or src.startswith("#") or _EXTERNAL.match(src):
        return match.group(0)

    target = (base / src).resolve()
    if not target.is_file():
        return f'<img src="{src}"{rest}>'

    width = _natural_width(target)
    scaled = f' width="{IMAGE_MAX}"' if width and width > IMAGE_MAX else ""
    return f'<img src="{target.as_uri()}"{scaled}{rest}>'


def _natural_width(path: Path) -> int:
    """Image width in px without decoding the pixels (header read only)."""
    reader = QImageReader(str(path))
    size = reader.size()
    return size.width() if size.isValid() else 0


def _table_rows(match: re.Match) -> str:
    """Rebuild a markdown table as valid HTML with borders and zebra rows.

    mistune emits ``<th>``/``<td>`` without wrapping ``<tr>``, which Qt cannot
    stripe (and may not lay out reliably), so the cells are regrouped into rows
    using the header's column count.
    """
    inner = match.group(1)
    head_match = _THEAD.search(inner)
    headers = _TH.findall(head_match.group(1)) if head_match else []
    if head_match:
        inner = inner[head_match.end():]

    columns = len(headers) or 1
    cells = _TD.findall(inner)

    head = ""
    if headers:
        head = (
            '<tr bgcolor="%s">%s</tr>'
            % (SURFACE, "".join(f'<th{attrs}>{body}</th>' for attrs, body in headers))
        )

    rows = []
    for start in range(0, len(cells), columns):
        chunk = cells[start:start + columns]
        colour = _TABLE_ROW_A if (start // columns) % 2 == 0 else _TABLE_ROW_B
        body = "".join(f"<td{attrs}>{cell}</td>" for attrs, cell in chunk)
        rows.append(f'<tr bgcolor="{colour}">{body}</tr>')

    return (
        '<table class="md" border="1" cellspacing="0" cellpadding="6">'
        f"{head}{''.join(rows)}</table>"
    )


def _heading_label(inner: str) -> str:
    """Heading text without markup: it ends up in a plain QListWidget."""
    return _SPACES.sub(" ", unescape(_TAGS.sub("", inner))).strip()


def _with_outline(html: str) -> tuple[str, list[Heading]]:
    """Inject an <a name> into every heading and collect the outline.

    Catches only real headings: mistune strips the raw tag to plain text inside
    fenced code, so a ``# comment`` in a code block is never matched here. The
    atx form (``# text``) is the only markdown heading, including setext.
    """
    outline: list[Heading] = []

    def replace(match: re.Match) -> str:
        level = int(match.group(1))
        anchor = f"md-h{len(outline)}"
        outline.append(Heading(level, _heading_label(match.group(3)), anchor))
        attrs = match.group(2) or ""
        return f'<h{level}{attrs}><a name="{anchor}"></a>{match.group(3)}</h{level}>'

    return _HEADING.sub(replace, html), outline


def render_with_outline(text: str, base_dir: Path | None = None) -> tuple[str, list[Heading]]:
    """Like `render`, but also returns the document outline."""
    html = _MARKDOWN(text)
    html = _CODE_BLOCK.sub(lambda m: _code_block(m.group(1), m.group(2)), html)
    html = _CHECKBOX.sub(_checkbox, html)
    html = _FOOTNOTES.sub(_footnotes, html)
    html = _IMAGE.sub(lambda m: _image(m, base_dir), html)
    html = _TABLE.sub(_table_rows, html)
    return _with_outline(html)


def render(text: str, base_dir: Path | None = None) -> str:
    """Convert markdown `text` (belonging to `base_dir`) to displayable HTML."""
    return render_with_outline(text, base_dir)[0]