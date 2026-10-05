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
# Neutral greys (QuickLook-like) rather than the earlier blue-violet tint: a
# document viewer should keep its chrome out of the way of the text.

BG = "#1b1b1b"          # page chrome (window, gutters)
PAGE_BG = "#212126"     # the reading surface: a touch lighter than the chrome
SURFACE = "#242424"     # quote, table header, footnotes
SURFACE_2 = "#2e2e2e"   # table header / hover
CODE_BG = "#141414"     # fenced code, editor background
CODE_INLINE_BG = "#2a2a2a"
BORDER = "#3a3a3a"
TEXT = "#e8e8e8"
TEXT_HEAD = "#ffffff"
TEXT_MUTED = "#9e9e9e"
ACCENT = "#5aa9ff"
ACCENT_DIM = "#3d6ea8"
CODE_TEXT = "#e6e6e6"
CODE_INLINE_TEXT = "#e0ab6e"
SUCCESS = "#7fd8a8"
MARK_BG = "#5c4a17"     # ==highlighted==
MARK_TEXT = "#ffe9a8"
SELECTION = "#2f5f9e"   # generic selection (find hits, line edits)
# Selection is colour-coded by the pane it started in, and the mirror in the
# other pane keeps that colour (mutual highlighting): pick in the preview and
# both panes show warm orange; pick in the markdown source and both show teal.
PREVIEW_SELECTION = "#b5712a"
EDITOR_SELECTION = "#2f8f8f"
# Qt paints the native selection *over* extraSelections, so a mirrored highlight
# is drawn by hand on top of the widget; this alpha keeps the text readable.
HL_OVERLAY_ALPHA = 130
SCROLL_HANDLE = "#3d3d3d"
SCROLL_HANDLE_HOVER = "#4d4d4d"

SANS = '"Segoe UI Variable Text", "Segoe UI", Inter, Arial, sans-serif'
MONO = '"Cascadia Mono", "Cascadia Code", Consolas, "Courier New", monospace'

# --- layout metrics (px) ---------------------------------------------------

MEASURE = 1300          # widest the text column is allowed to get
GUTTER_MIN = 20         # inset when the window is too narrow for MEASURE
MARGIN_STEP = 8         # don't re-layout the document for sub-step resizes
MIN_COLUMN = 420        # soft minimum of the reading column / preview pane
PREVIEW_HARD = 260      # hard floor: the window cannot squeeze the preview below it
IMAGE_MAX = 860         # widest an <img> is emitted at (renderer._image)
SCREEN_FALLBACK = 1700  # assumed screen width when QGuiApplication reports none

# Pane shrink policy (viewer.layout_panes). When the window is too narrow the
# panes give width up in priority order TOC -> editor -> preview, each down to
# its hard floor; the preview is the residual pane and keeps MIN_COLUMN as long
# as possible. Preferred widths are plain constants (never derived from the
# window) so opening edit once on a narrow window cannot bake in a bad layout.
TOC_WIDTH = 240         # default / preferred outline width (px)
TOC_MIN_WIDTH = 120     # outline shrinks to this, then auto-hides
TOC_STEP = 14           # indent per heading level
TOC_ROW = "#0f0f0f"     # current section highlight in the outline
EDITOR_PREF = 520       # preferred editor width; the preview is residual by design
EDITOR_MIN = 360        # editor yields to this before the preview drops below MIN_COLUMN
EDITOR_HARD = 220       # hard floor for the editor
SPLIT_HANDLE = 14       # splitter grab width (px): the panes' edges are easier to catch
HYST = 40               # auto-hide hysteresis: no flicker at the show/hide threshold


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
    background-color: {PAGE_BG};
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

p {{ margin-top: 18px; margin-bottom: 18px; }}

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
    font-size: 0.9em;
    color: {CODE_INLINE_TEXT};
    background-color: {CODE_INLINE_BG};
}}

/* Code cards. `text-indent` is the only inset Qt honours on a block (padding is
   ignored), so it stands in for the left padding; the vertical breathing room
   comes from the line-height leading. Do NOT wrap the code in a table: Qt's
   hit-testing inside a multi-line cell is broken, so clicking one code line
   selected from the bottom of the card. */
p.code-lang {{
    background-color: {CODE_BG};
    color: {TEXT_MUTED};
    font-family: {SANS};
    font-size: 11px;
    line-height: 150%;
    margin-top: 16px;
    margin-bottom: 0;
    text-indent: 14px;
}}

pre.code-body {{
    font-family: {MONO};
    font-size: 0.9em;
    line-height: 158%;
    color: {CODE_TEXT};
    background-color: {CODE_BG};
    white-space: pre-wrap;
    margin-top: 16px;
    margin-bottom: 16px;
    text-indent: 14px;
}}
pre.code-body.has-lang {{ margin-top: 0; }}

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
/* Tabs follow the dark chrome: the bar sits on the window colour, the active
   tab blends into the page while the others read as raised cards. */
QTabWidget::pane {{
    border: none;
    background-color: {BG};
}}
QTabWidget::tab-bar {{
    left: 0;
}}
QTabBar {{
    background-color: {BG};
    qproperty-drawBase: 0;
}}
QTabBar::tab {{
    background-color: {SURFACE};
    color: {TEXT_MUTED};
    border: 1px solid {BORDER};
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 5px 12px;
    margin-right: 2px;
    min-width: 90px;
}}
QTabBar::tab:selected {{
    background-color: {PAGE_BG};
    color: {TEXT_HEAD};
    border-color: {BORDER};
}}
QTabBar::tab:hover:!selected {{
    background-color: {SURFACE_2};
    color: {TEXT};
}}
QTabBar::close-button {{
    subcontrol-position: right;
}}
/* The splitter handle is the pane divider: keep it wide enough to grab easily
   and light it up under the pointer so the movable edge is obvious. */
QSplitter::handle {{
    background-color: {BG};
}}
QSplitter::handle:hover {{
    background-color: {ACCENT_DIM};
}}
QSplitter::handle:pressed {{
    background-color: {ACCENT};
}}
QTextBrowser {{
    background-color: {BG};
    border: none;
    color: {TEXT};
    selection-background-color: {PREVIEW_SELECTION};
    selection-color: #ffffff;
}}
QScrollBar:vertical {{
    background-color: {BG};
    width: 9px;
    margin: 0;
    border: none;
}}
QScrollBar::handle:vertical {{
    background-color: {SCROLL_HANDLE};
    min-height: 32px;
    border-radius: 4px;
    margin: 2px;
}}
QScrollBar::handle:vertical:hover {{ background-color: {SCROLL_HANDLE_HOVER}; }}
QScrollBar:horizontal {{
    background-color: {BG};
    height: 9px;
    margin: 0;
    border: none;
}}
QScrollBar::handle:horizontal {{
    background-color: {SCROLL_HANDLE};
    min-width: 32px;
    border-radius: 4px;
    margin: 2px;
}}
QScrollBar::handle:horizontal:hover {{ background-color: {SCROLL_HANDLE_HOVER}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background-color: transparent; }}

QLineEdit {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 5px;
    padding: 3px 8px;
    color: {TEXT};
    selection-background-color: {SELECTION};
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
    background-color: #1f1f1f;
    color: #5f5f5f;
    border-color: #2a2a2a;
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
    selection-background-color: {EDITOR_SELECTION};
    selection-color: #ffffff;
}}
#editorPanel {{ background-color: {CODE_BG}; }}

QListWidget#outline {{
    background-color: {BG};
    border: none;
    border-right: 1px solid {BORDER};
    outline: none;
    color: {TEXT_MUTED};
    font-size: 13px;
}}
QListWidget#outline::item {{
    padding: 3px 8px;
    border-radius: 4px;
}}
QListWidget#outline::item:hover {{
    background-color: {SURFACE};
    color: {TEXT};
}}
QListWidget#outline::item:selected {{
    background-color: {SURFACE_2};
    color: {TEXT_HEAD};
}}

#overlay {{
    background-color: rgba(38, 38, 38, 235);
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
#overlay QToolButton {{
    color: {TEXT};
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 14px;
}}
#overlay QToolButton:hover {{
    background-color: {SURFACE_2};
    border-color: {ACCENT_DIM};
}}

#editorPanel QToolButton {{
    color: {TEXT};
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 3px 8px;
}}
#editorPanel QToolButton:hover {{
    background-color: {SURFACE_2};
    border-color: {ACCENT_DIM};
}}

/* Filter row above the outline. Typing here reveals the outline and hides the
   headings that do not match. */
QLineEdit#tocFilter {{
    background-color: {SURFACE};
    border: none;
    border-bottom: 1px solid {BORDER};
    border-radius: 0;
    padding: 4px 8px;
    color: {TEXT};
    font-size: 12px;
    selection-background-color: {SELECTION};
}}
QLineEdit#tocFilter:focus {{ border-bottom: 1px solid {ACCENT_DIM}; }}

/* Breadcrumb strip above the preview: the heading path of the current section.
   Ancestor segments are links (inline colour), the last one is the accent. */
QLabel#breadcrumbs {{
    background-color: {BG};
    color: {TEXT_MUTED};
    font-size: 11px;
    padding: 4px 12px;
    border-bottom: 1px solid {BORDER};
}}
"""