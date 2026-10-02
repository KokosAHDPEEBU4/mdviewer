"""Dark theme: colours, font stacks and the stylesheet for the document.

Qt renders the document with its own rich-text engine, which supports only a
small subset of CSS 2.1.  The rules that actually work (and are used below):

  colour, background-color, font-*, line-height, margin-* (block elements),
  text-align, text-decoration, white-space, vertical-align, list markers

Deliberately *not* used, because Qt silently ignores them:

  CSS custom properties (--var), padding on block elements, border on block
  elements, max-width, `margin: 0 auto`, transitions, ::before/::after.

Consequences baked into the layout: vertical rhythm comes from `margin`, the
horizontal inset of the text column from QTextBrowser viewport margins (set in
viewer.Viewer, because changing them does not re-parse the document), and
anything that would need real inner padding (code blocks) is rendered as a
single-cell table by `renderer.py`.
"""

# --- palette ---------------------------------------------------------------

BG = "#15151a"          # page
SURFACE = "#1e1e25"     # quote, table header, footnotes
SURFACE_2 = "#26262f"    # table header / hover
CODE_BG = "#101015"     # fenced code, editor background
CODE_INLINE_BG = "#22222b"
BORDER = "#2e2e39"
TEXT = "#e2e2e8"
TEXT_HEAD = "#f4f4f7"
TEXT_MUTED = "#9a9aa6"
ACCENT = "#6cb2ff"
ACCENT_DIM = "#3f6fa8"
CODE_TEXT = "#e6e6ec"
CODE_INLINE_TEXT = "#ffc38b"
SUCCESS = "#79e2b6"
MARK_BG = "#6a5518"     # ==highlighted==
MARK_TEXT = "#ffe9a8"

SANS = '"Segoe UI Variable Text", "Segoe UI", Inter, Arial, sans-serif'
MONO = '"Cascadia Mono", "Cascadia Code", Consolas, "Courier New", monospace'

# --- layout metrics (px) ---------------------------------------------------

MEASURE = 1300          # widest the text column is allowed to get
GUTTER_MIN = 20         # inset when the window is too narrow for MEASURE
MARGIN_STEP = 8         # don't re-layout the document for sub-step resizes
MIN_COLUMN = 420        # never let the insets squeeze the text below this
EDIT_WIDTH = 1700       # window width the editor asks for (clamped to the screen)
EDIT_SPLIT = 0.55       # share of the window the preview keeps in edit mode
IMAGE_MAX = 860         # widest an <img> is emitted at (renderer._image)


def side_margin(width: int) -> int:
    """Inset that centres the text column (QTextBrowser viewport margins, so it
    costs nothing to change on resize).

    A narrow window spends its width on text and keeps the insets minimal; the
    column stops growing at MEASURE px and everything above that becomes
    symmetric side bars -- around 12% each on a 1700 px window, ~7% on 2600 px.

    The MIN_COLUMN clamp matters in edit mode: the preview column shrinks
    without the window being resized, so a margin computed for the old width
    would otherwise eat the whole column.
    """
    slack = width - min(width - GUTTER_MIN * 2, MEASURE)
    if slack <= GUTTER_MIN * 2:
        return GUTTER_MIN
    side = round(slack / 2 / MARGIN_STEP) * MARGIN_STEP
    return min(side, max(GUTTER_MIN, (width - MIN_COLUMN) // 2))


def content_css() -> str:
    """Stylesheet for the rendered document."""
    return f"""
body {{
    margin-top: 0;
    margin-bottom: 48px;
    color: {TEXT};
    background-color: {BG};
    font-family: {SANS};
    font-size: 16px;
    line-height: 168%;
}}

h1, h2, h3, h4, h5, h6 {{
    color: {TEXT_HEAD};
    font-weight: 600;
    margin-left: 0;
    margin-right: 0;
}}
h1 {{
    font-size: 31px;
    line-height: 130%;
    margin-top: 8px;
    margin-bottom: 18px;
}}
h2 {{
    font-size: 24px;
    line-height: 135%;
    margin-top: 34px;
    margin-bottom: 12px;
}}
h3 {{
    font-size: 19.5px;
    line-height: 140%;
    margin-top: 26px;
    margin-bottom: 10px;
}}
h4 {{
    font-size: 17px;
    line-height: 145%;
    margin-top: 22px;
    margin-bottom: 8px;
}}
h5, h6 {{
    font-size: 15.5px;
    line-height: 150%;
    margin-top: 18px;
    margin-bottom: 6px;
}}
h1 + p, h2 + p, h3 + p, h4 + p {{ margin-top: 0; }}

p {{ margin-top: 12px; margin-bottom: 12px; }}

a {{
    color: {ACCENT};
    text-decoration: none;
}}
a:hover {{ text-decoration: underline; }}

strong {{ color: {TEXT_HEAD}; font-weight: 600; }}
em {{ font-style: italic; }}
del, s, strike {{ color: {TEXT_MUTED}; text-decoration: line-through; }}
mark {{ background-color: {MARK_BG}; color: {MARK_TEXT}; }}
abbr {{ text-decoration: underline; }}
sup, sub {{ color: {TEXT_MUTED}; font-size: 78%; }}

ul, ol {{ margin-top: 12px; margin-bottom: 12px; margin-left: 26px; }}
ul li, ol li {{ margin-top: 4px; margin-bottom: 4px; }}
li .task-box {{ color: {ACCENT}; }}
li .task-box-done {{ color: {SUCCESS}; }}

blockquote {{
    margin-top: 16px;
    margin-bottom: 16px;
    margin-left: 14px;
    margin-right: 14px;
    background-color: {SURFACE};
    color: {TEXT_MUTED};
}}
blockquote p {{ margin-top: 8px; margin-bottom: 8px; }}

hr {{
    margin-top: 26px;
    margin-bottom: 26px;
    color: {BORDER};
    height: 1px;
}}

img {{ margin-top: 6px; margin-bottom: 6px; }}

code {{
    font-family: {MONO};
    font-size: 13.5px;
    color: {CODE_INLINE_TEXT};
    background-color: {CODE_INLINE_BG};
}}
pre {{
    font-family: {MONO};
    font-size: 13.5px;
    line-height: 158%;
    color: {CODE_TEXT};
    background-color: {CODE_BG};
    white-space: pre-wrap;
    margin-top: 0;
    margin-bottom: 0;
}}
pre code {{
    font-size: 14px;
    color: {CODE_TEXT};
    background-color: transparent;
}}

/* code cards: one-cell tables give real inner padding (blocks have none) */
table.code {{ margin-top: 16px; margin-bottom: 16px; }}
table.code td {{ background-color: {CODE_BG}; border: none; }}
table.code td.code-lang {{
    background-color: {CODE_BG};
    color: {TEXT_MUTED};
    font-family: {SANS};
    font-size: 11px;
    line-height: 130%;
}}
table.code td.code-body {{ color: {CODE_TEXT}; }}

table.md {{ margin-top: 16px; margin-bottom: 16px; }}
table.md th {{
    background-color: {SURFACE_2};
    color: {TEXT_HEAD};
    font-weight: 600;
    text-align: left;
    border: 1px solid {BORDER};
}}
table.md td {{ border: 1px solid {BORDER}; color: {TEXT}; }}

dl {{ margin-top: 12px; margin-bottom: 12px; margin-left: 14px; }}
dt {{ color: {TEXT_HEAD}; font-weight: 600; margin-top: 8px; }}
dd {{ margin-left: 20px; color: {TEXT}; }}

.footnotes {{ margin-top: 30px; margin-left: 14px; color: {TEXT_MUTED}; }}
.footnotes a {{ color: {ACCENT_DIM}; }}
"""


APP_QSS = f"""
QMainWindow {{
    background-color: {BG};
}}
QTextBrowser {{
    background-color: {BG};
    border: none;
    color: {TEXT};
    selection-background-color: #2f5f9e;
    selection-color: #ffffff;
}}
QScrollBar:vertical {{
    background-color: {BG};
    width: 13px;
    margin: 0;
    border: none;
}}
QScrollBar::handle:vertical {{
    background-color: #34343f;
    min-height: 32px;
    border-radius: 5px;
    margin: 3px;
}}
QScrollBar::handle:vertical:hover {{ background-color: #45454f; }}
QScrollBar:horizontal {{
    background-color: {BG};
    height: 13px;
    margin: 0;
    border: none;
}}
QScrollBar::handle:horizontal {{
    background-color: #34343f;
    min-width: 32px;
    border-radius: 5px;
    margin: 3px;
}}
QScrollBar::handle:horizontal:hover {{ background-color: #45454f; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background-color: transparent; }}

QLineEdit {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 5px;
    padding: 3px 8px;
    color: {TEXT};
    selection-background-color: #2f5f9e;
}}
QLineEdit:focus {{ border: 1px solid {ACCENT_DIM}; }}
QToolButton {{
    background-color: transparent;
    border: none;
    border-radius: 4px;
    color: {TEXT_MUTED};
    padding: 3px 9px;
}}
QToolButton:hover {{ background-color: {SURFACE}; color: {TEXT}; }}

QPushButton {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 5px;
    padding: 4px 12px;
    color: {TEXT};
}}
QPushButton:hover:!disabled {{ background-color: {SURFACE_2}; }}
QPushButton:disabled {{
    background-color: #191920;
    color: #5c5c68;
    border-color: #232330;
}}
QPushButton#primary {{
    background-color: {ACCENT_DIM};
    border-color: {ACCENT_DIM};
    color: #ffffff;
}}
QPushButton#primary:hover:!disabled {{ background-color: {ACCENT}; }}

QPlainTextEdit {{
    background-color: {CODE_BG};
    border: none;
    border-top: 1px solid {BORDER};
    color: {CODE_TEXT};
    font-family: {MONO};
    font-size: 13.5px;
    selection-background-color: #2f5f9e;
    selection-color: #ffffff;
}}
#editorPanel {{ background-color: {CODE_BG}; }}
"""