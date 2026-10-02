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
from pathlib import Path

import mistune
from PyQt6.QtGui import QImageReader

from theme import MEASURE

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

_TASK_OPEN, _TASK_DONE = '<span class="task-box">&#9744;</span> ', (
    '<span class="task-box-done">&#9745;</span> '
)


def _code_block(lang: str | None, body: str) -> str:
    """Fenced code -> single-cell table (real padding) + language caption."""
    label = f"{(lang or '').removeprefix('language-').strip()}"
    head = (
        f'<tr><td class="code-lang">{label}</td></tr>' if label else ""
    )
    inner = (
        f'<pre><code class="{lang}">{body}</code></pre>'
        if lang
        else f"<pre>{body}</pre>"
    )
    return (
        '<table class="code">'
        f'{head}<tr><td class="code-body">{inner}</td></tr>'
        "</table>"
    )


def _checkbox(match: re.Match) -> str:
    return _TASK_DONE if "checked" in match.group(0) else _TASK_OPEN


def _footnotes(match: re.Match) -> str:
    return (
        '<div class="footnotes">'
        '<p class="footnotes-title">Notes</p>'
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
    scaled = f' width="{MEASURE}"' if width and width > MEASURE else ""
    return f'<img src="{target.as_uri()}"{scaled}{rest}>'


def _natural_width(path: Path) -> int:
    """Image width in px without decoding the pixels (header read only)."""
    reader = QImageReader(str(path))
    size = reader.size()
    return size.width() if size.isValid() else 0


def render(text: str, base_dir: Path | None = None) -> str:
    """Convert markdown `text` (belonging to `base_dir`) to displayable HTML."""
    html = _MARKDOWN(text)
    html = _CODE_BLOCK.sub(lambda m: _code_block(m.group(1), m.group(2)), html)
    html = _CHECKBOX.sub(_checkbox, html)
    html = _FOOTNOTES.sub(_footnotes, html)
    html = _IMAGE.sub(lambda m: _image(m, base_dir), html)
    return html