"""Themes: colours, font stacks and the stylesheets for the document / app.

Two palettes live here, ``dark`` (the default) and ``light``.  A theme name is
stored in ``QSettings("theme")``; ``content_css(theme)`` and ``app_qss(theme)``
build the stylesheets for it, while the bare module constants below mirror the
dark palette so the rest of the app (and the tools) keep importing them
unchanged.  ``content_css()`` / ``APP_QSS`` without an argument are therefore
exactly the old dark stylesheet — the dark theme stays pixel-identical.

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

from pathlib import Path

# --- palettes --------------------------------------------------------------
# Every token a stylesheet below references must exist in *both* palettes, or
# building that stylesheet raises KeyError.  Values are plain Qt colour strings
# (hex or `rgba(...)`).

DARK = {
    "BG": "#1b1b1b",                    # page chrome (window, gutters)
    "PAGE_BG": "#212126",               # reading surface: a touch lighter
    "SURFACE": "#242424",               # quote, table header, footnotes
    "SURFACE_2": "#2e2e2e",             # table header / hover
    "CODE_BG": "#141414",               # fenced code, editor background
    "CODE_INLINE_BG": "#2a2a2a",
    "BORDER": "#3a3a3a",
    "TEXT": "#e8e8e8",
    "TEXT_HEAD": "#ffffff",
    "TEXT_MUTED": "#9e9e9e",
    "ACCENT": "#5aa9ff",
    "ACCENT_DIM": "#3d6ea8",
    "CODE_TEXT": "#e6e6e6",
    "CODE_INLINE_TEXT": "#e0ab6e",
    # JSON syntax colours (renderer.render_json). Keep in both palettes: every
    # token referenced by content_css must exist in DARK and LIGHT (invariant 32).
    "JSON_KEY": "#9cdcfe",
    "JSON_STRING": "#ce9178",
    "JSON_NUMBER": "#b5cea8",
    "JSON_BOOL": "#569cd6",
    "JSON_NULL": "#c586c0",
    "JSON_ERROR": "#f48771",
    "SUCCESS": "#7fd8a8",
    "MARK_BG": "#5c4a17",               # ==highlighted==
    "MARK_TEXT": "#ffe9a8",
    "SELECTION": "#2f5f9e",             # generic selection (find hits, line edits)
    # Selection is colour-coded by the pane it started in, and the mirror in the
    # other pane keeps that colour (mutual highlighting): pick in the preview
    # and both panes show warm orange; pick in the markdown source and both
    # show teal.
    "PREVIEW_SELECTION": "#b5712a",
    "EDITOR_SELECTION": "#2f8f8f",
    "SCROLL_HANDLE": "#3d3d3d",
    "SCROLL_HANDLE_HOVER": "#4d4d4d",
    "SEL_TEXT": "#ffffff",              # colour of selected text
    "OVERLAY_BG": "rgba(38, 38, 38, 235)",
    "DISABLED_BG": "#1f1f1f",
    "DISABLED_TEXT": "#5f5f5f",
    "DISABLED_BORDER": "#2a2a2a",
    "PRIMARY_TEXT": "#ffffff",
    # Table zebra / header. renderer.py bakes the header (`SURFACE`) and the
    # rows into `bgcolor` attributes; `retheme_tables` maps those dark values
    # onto the active palette at render time (renderer itself is untouched).
    "TABLE_HEADER": "#242424",
    "TABLE_ROW_A": "#1f1f1f",
    "TABLE_ROW_B": "#242424",
    "SANS": '"Segoe UI Variable Text", "Segoe UI", Inter, Arial, sans-serif',
    "MONO": '"Cascadia Mono", "Cascadia Code", Consolas, "Courier New", monospace',
    # Tab close-button images (non-colour tokens, like the font stacks): QSS
    # cannot move the native button widget, so the right-edge gap is baked
    # into the PNGs (tools/make_close_icons.py).
    "CLOSE_TAB": "close_tab_dark.png",
    "CLOSE_TAB_HOVER": "close_tab_dark_hover.png",
}

LIGHT = {
    "BG": "#f2f2f2",
    "PAGE_BG": "#ffffff",
    "SURFACE": "#f0f0f0",
    "SURFACE_2": "#e6e6e6",
    "CODE_BG": "#f5f5f5",
    "CODE_INLINE_BG": "#ececec",
    "BORDER": "#d2d2d2",
    "TEXT": "#202020",
    "TEXT_HEAD": "#000000",
    "TEXT_MUTED": "#6a6a6a",
    "ACCENT": "#1668c4",
    "ACCENT_DIM": "#3f7fc0",
    "CODE_TEXT": "#242424",
    "CODE_INLINE_TEXT": "#9a4a00",
    "JSON_KEY": "#0451a5",
    "JSON_STRING": "#a31515",
    "JSON_NUMBER": "#098658",
    "JSON_BOOL": "#0000ff",
    "JSON_NULL": "#af00db",
    "JSON_ERROR": "#c62828",
    "SUCCESS": "#1f8a4c",
    "MARK_BG": "#ffe58a",
    "MARK_TEXT": "#5c4a17",
    "SELECTION": "#5b86c7",
    # The pane-coded highlights stay dark enough for white text on a light page.
    "PREVIEW_SELECTION": "#c07a2a",
    "EDITOR_SELECTION": "#2b8a8a",
    "SCROLL_HANDLE": "#c0c0c0",
    "SCROLL_HANDLE_HOVER": "#a2a2a2",
    "SEL_TEXT": "#ffffff",
    "OVERLAY_BG": "rgba(250, 250, 250, 238)",
    "DISABLED_BG": "#e2e2e2",
    "DISABLED_TEXT": "#a0a0a0",
    "DISABLED_BORDER": "#d2d2d2",
    "PRIMARY_TEXT": "#ffffff",
    "TABLE_HEADER": "#e8e8e8",
    "TABLE_ROW_A": "#f7f7f7",
    "TABLE_ROW_B": "#ffffff",
    "SANS": DARK["SANS"],
    "MONO": DARK["MONO"],
    "CLOSE_TAB": "close_tab_light.png",
    "CLOSE_TAB_HOVER": "close_tab_light_hover.png",
}

PALETTES = {"dark": DARK, "light": LIGHT}
DEFAULT_THEME = "dark"
THEMES = ("dark", "light")

# Opacity of the hand-drawn mirror highlight (invariant 22).  Kept out of the
# palettes: it is not referenced by any stylesheet.
HL_OVERLAY_ALPHA = 130


def palette(theme: str = DEFAULT_THEME) -> dict:
    """The colour tokens for `theme` (unknown names fall back to dark)."""
    return PALETTES.get(theme, PALETTES[DEFAULT_THEME])


def normalize(theme) -> str:
    """Coerce anything to a valid theme name (default dark)."""
    return theme if theme in PALETTES else DEFAULT_THEME


def is_light(theme: str) -> bool:
    return normalize(theme) == "light"


def retheme_tables(html: str, theme: str = DEFAULT_THEME) -> str:
    """Swap the table zebra/header colours baked in by `renderer.py`.

    renderer emits `bgcolor` attributes copied from the dark palette; Qt gives
    those attributes priority over CSS, so on the light theme the rows would
    stay dark under dark text.  The header row is replaced first (it carries
    `<th`) because its dark value equals TABLE_ROW_B's.
    """
    if normalize(theme) == DEFAULT_THEME:
        return html
    light = PALETTES[theme]
    html = html.replace(
        f'<tr bgcolor="{DARK["TABLE_HEADER"]}"><th',
        f'<tr bgcolor="{light["TABLE_HEADER"]}"><th',
    )
    html = html.replace(
        f'<tr bgcolor="{DARK["TABLE_ROW_A"]}">',
        f'<tr bgcolor="{light["TABLE_ROW_A"]}">',
    )
    html = html.replace(
        f'<tr bgcolor="{DARK["TABLE_ROW_B"]}">',
        f'<tr bgcolor="{light["TABLE_ROW_B"]}">',
    )
    return html


# --- dark module constants (backwards compatible) --------------------------
# viewer/renderer/main/render_check import these directly; they must keep their
# dark values, and `content_css()` / `APP_QSS` must be the dark stylesheets.

BG = DARK["BG"]
PAGE_BG = DARK["PAGE_BG"]
SURFACE = DARK["SURFACE"]
SURFACE_2 = DARK["SURFACE_2"]
CODE_BG = DARK["CODE_BG"]
CODE_INLINE_BG = DARK["CODE_INLINE_BG"]
BORDER = DARK["BORDER"]
TEXT = DARK["TEXT"]
TEXT_HEAD = DARK["TEXT_HEAD"]
TEXT_MUTED = DARK["TEXT_MUTED"]
ACCENT = DARK["ACCENT"]
ACCENT_DIM = DARK["ACCENT_DIM"]
CODE_TEXT = DARK["CODE_TEXT"]
CODE_INLINE_TEXT = DARK["CODE_INLINE_TEXT"]
SUCCESS = DARK["SUCCESS"]
MARK_BG = DARK["MARK_BG"]
MARK_TEXT = DARK["MARK_TEXT"]
SELECTION = DARK["SELECTION"]
PREVIEW_SELECTION = DARK["PREVIEW_SELECTION"]
EDITOR_SELECTION = DARK["EDITOR_SELECTION"]
SCROLL_HANDLE = DARK["SCROLL_HANDLE"]
SCROLL_HANDLE_HOVER = DARK["SCROLL_HANDLE_HOVER"]

SANS = DARK["SANS"]
MONO = DARK["MONO"]

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
TREE_WIDTH = 220        # default width of the «Файлы папки» panel (px)
TREE_MIN_WIDTH = 140    # the file-tree panel never shrinks below this
EDITOR_PREF = 520       # fallback editor width (kept for tools/compat)
EDITOR_MIN = 360        # editor yields to this before the preview drops below MIN_COLUMN
EDITOR_HARD = 220       # hard floor for the editor
SPLIT_HANDLE = 14       # splitter grab width (px): the panes' edges are easier to catch
HYST = 40               # auto-hide hysteresis: no flicker at the show/hide threshold

# Edit-mode split is stored as a *ratio* editor/(editor+preview), not a fixed
# width, so 27" and 34" monitors look the same (B3). The window in edit mode is
# capped at the width where the preview is exactly MEASURE (B2): `EDITOR_MAX`
# stops the code pane from getting absurdly wide on an ultrawide.
EDITOR_RATIO = 0.30     # default share of the preview+editor area for the editor
EDITOR_MAX = 960        # editor never grows past this, even on a huge window
# Smallest window the edit target is allowed to shrink to; below this the pane
# shrink policy (layout_panes) would already be at its hard floors.
MIN_WINDOW_WIDTH = TOC_MIN_WIDTH + PREVIEW_HARD + EDITOR_HARD + SPLIT_HANDLE * 2


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


def content_css(theme: str = DEFAULT_THEME) -> str:
    """Stylesheet for the rendered document of `theme`."""
    # Local names shadow the dark module constants above, so the CSS body stays
    # byte-identical for the dark theme (the default).
    p = palette(theme)
    SANS = p["SANS"]
    MONO = p["MONO"]
    TEXT = p["TEXT"]
    PAGE_BG = p["PAGE_BG"]
    TEXT_HEAD = p["TEXT_HEAD"]
    ACCENT = p["ACCENT"]
    TEXT_MUTED = p["TEXT_MUTED"]
    MARK_BG = p["MARK_BG"]
    MARK_TEXT = p["MARK_TEXT"]
    SUCCESS = p["SUCCESS"]
    SURFACE = p["SURFACE"]
    BORDER = p["BORDER"]
    CODE_INLINE_TEXT = p["CODE_INLINE_TEXT"]
    CODE_INLINE_BG = p["CODE_INLINE_BG"]
    CODE_BG = p["CODE_BG"]
    CODE_TEXT = p["CODE_TEXT"]
    SURFACE_2 = p["SURFACE_2"]
    ACCENT_DIM = p["ACCENT_DIM"]
    JSON_KEY = p["JSON_KEY"]
    JSON_STRING = p["JSON_STRING"]
    JSON_NUMBER = p["JSON_NUMBER"]
    JSON_BOOL = p["JSON_BOOL"]
    JSON_NULL = p["JSON_NULL"]
    JSON_ERROR = p["JSON_ERROR"]
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

/* JSON: pretty-printed inside a normal code card, with syntax colours. The
   classes are emitted by renderer.render_json; Qt matches simple class
   selectors fine, so no descendant combinators are needed. */
.json-key {{ color: {JSON_KEY}; }}
.json-string {{ color: {JSON_STRING}; }}
.json-number {{ color: {JSON_NUMBER}; }}
.json-bool {{ color: {JSON_BOOL}; }}
.json-null {{ color: {JSON_NULL}; }}
p.json-error {{
    color: {JSON_ERROR};
    margin-top: 12px;
    margin-bottom: 8px;
}}

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


def _asset(name: str) -> str:
    """Absolute posix path for a QSS `url()`: relative image paths resolve
    against the process working directory, which varies between launches."""
    return (Path(__file__).resolve().parent / "assets" / name).as_posix()


def _close_button_qss(theme: str) -> str:
    """The tab close-button rules, built apart from `app_qss` on purpose.

    The image paths are runtime file names (palette tokens, resolved through
    `_asset`), not stylesheet colours, so they must not appear as `{tokens}`
    inside the big f-string. The icon PNG carries the gap to the tab's right
    wall inside it: QSS cannot move the native button widget (`position`,
    `margin` and `subcontrol-origin` are all ignored for the subcontrol), so
    the ✕ is drawn shifted left on a transparent canvas
    (tools/make_close_icons.py).
    """
    p = palette(theme)
    return (
        "QTabBar::close-button {\n"
        f'    image: url("{_asset(p["CLOSE_TAB"])}");\n'
        "    subcontrol-position: right center;\n"
        "}\n"
        "QTabBar::close-button:hover {\n"
        f'    image: url("{_asset(p["CLOSE_TAB_HOVER"])}");\n'
        "}\n"
    )


def app_qss(theme: str = DEFAULT_THEME) -> str:
    """Application stylesheet (chrome, panes, overlay) for `theme`."""
    p = palette(theme)
    # Local names shadow the dark module constants; the dark output is unchanged.
    BG = p["BG"]
    SURFACE = p["SURFACE"]
    TEXT_MUTED = p["TEXT_MUTED"]
    BORDER = p["BORDER"]
    PAGE_BG = p["PAGE_BG"]
    TEXT_HEAD = p["TEXT_HEAD"]
    TEXT = p["TEXT"]
    SURFACE_2 = p["SURFACE_2"]
    ACCENT_DIM = p["ACCENT_DIM"]
    ACCENT = p["ACCENT"]
    PREVIEW_SELECTION = p["PREVIEW_SELECTION"]
    SCROLL_HANDLE = p["SCROLL_HANDLE"]
    SCROLL_HANDLE_HOVER = p["SCROLL_HANDLE_HOVER"]
    SEL_TEXT = p["SEL_TEXT"]
    SELECTION = p["SELECTION"]
    CODE_BG = p["CODE_BG"]
    CODE_TEXT = p["CODE_TEXT"]
    MONO = p["MONO"]
    EDITOR_SELECTION = p["EDITOR_SELECTION"]
    DISABLED_BG = p["DISABLED_BG"]
    DISABLED_TEXT = p["DISABLED_TEXT"]
    DISABLED_BORDER = p["DISABLED_BORDER"]
    PRIMARY_TEXT = p["PRIMARY_TEXT"]
    OVERLAY_BG = p["OVERLAY_BG"]
    qss = f"""
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
    subcontrol-position: right center;
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
    selection-color: {SEL_TEXT};
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
/* Settings dialog: combos and checkboxes sit on the window surface. */
QDialog {{
    background-color: {BG};
    color: {TEXT};
}}
QDialog QLabel {{ color: {TEXT}; }}
QComboBox {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 5px;
    padding: 3px 8px;
    color: {TEXT};
    min-width: 120px;
}}
QComboBox:hover {{ border-color: {ACCENT_DIM}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    color: {TEXT};
    selection-background-color: {SELECTION};
    selection-color: {SEL_TEXT};
    outline: none;
}}
QCheckBox {{ color: {TEXT}; spacing: 8px; }}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid {BORDER};
    border-radius: 3px;
    background-color: {SURFACE};
}}
QCheckBox::indicator:checked {{
    background-color: {ACCENT_DIM};
    border-color: {ACCENT_DIM};
}}
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
    background-color: {DISABLED_BG};
    color: {DISABLED_TEXT};
    border-color: {DISABLED_BORDER};
}}
QPushButton#primary {{
    background-color: {ACCENT_DIM};
    border-color: {ACCENT_DIM};
    color: {PRIMARY_TEXT};
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
    selection-color: {SEL_TEXT};
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

/* The «Файлы папки» panel: the active tab's folder. Hidden by default; the
   header carries the caption and the close button, the folder path sits in
   its own badge, the list mirrors the outline styling. */
QWidget#treePanel {{
    background-color: {BG};
    border-right: 1px solid {BORDER};
}}
QLabel#treeCaption {{
    color: {TEXT_MUTED};
    font-size: 11px;
}}
QLabel#treeFolder {{
    color: {TEXT};
    font-size: 12px;
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 3px 8px;
    margin: 0 10px 8px;
}}
QListWidget#treeList {{
    background-color: {BG};
    border: none;
    outline: none;
    color: {TEXT};
    font-size: 13px;
}}
QListWidget#treeList::item {{
    padding: 3px 8px;
    border-radius: 4px;
}}
QListWidget#treeList::item:hover {{
    background-color: {SURFACE};
}}
QListWidget#treeList::item:selected {{
    background-color: {SURFACE_2};
    color: {TEXT_HEAD};
}}

#overlay {{
    background-color: {OVERLAY_BG};
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
/* Find / replace strip inside the overlay. The replace row appears only for
   Ctrl+H; both rows are transparent so the overlay's own background shows. */
#findBar, #replaceRow {{ background-color: transparent; }}
#replaceRow QLineEdit {{ background-color: {SURFACE}; }}
#replaceRow QPushButton {{ padding: 3px 10px; font-size: 12px; }}

/* The copy strip that follows a preview selection. [stale] marks markdown
   that no longer exists in the editable source: it is copied from the
   render-time cache and the button says so ("с md (удалено)"). */
#copyPopup {{
    background-color: {OVERLAY_BG};
    border: 1px solid {BORDER};
    border-radius: 6px;
}}
#copyPopup QToolButton {{
    color: {TEXT_HEAD};
    background-color: transparent;
    border: none;
    border-radius: 4px;
    padding: 3px 8px;
    font-size: 12px;
}}
#copyPopup QToolButton:hover {{ background-color: {SURFACE_2}; }}
#copyPopup QToolButton[stale="true"] {{ color: {TEXT_MUTED}; font-style: italic; }}
#copyPopup QToolButton[stale="true"]:hover {{ color: {TEXT}; }}

/* Copy button over a fenced code block: it appears on hover in the card's
   top-right corner, and flashes "Скопировано" after a click. */
#codeCopy {{
    color: {TEXT_MUTED};
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 5px;
    padding: 2px 8px;
    font-size: 11px;
}}
#codeCopy:hover {{ background-color: {SURFACE_2}; color: {TEXT}; }}
#codeCopy[copied="true"] {{ color: {ACCENT}; border-color: {ACCENT_DIM}; }}

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

/* The strip above the preview: heading crumbs on the left, the file's own
   path on the right (a click opens the containing folder). The strip owns the
   background and the bottom border so both halves read as one bar. */
QWidget#pathStrip {{
    background-color: {BG};
    border-bottom: 1px solid {BORDER};
}}
QLabel#breadcrumbs {{
    background-color: transparent;
    color: {TEXT_MUTED};
    font-size: 11px;
    padding: 4px 0 4px 12px;
    border: none;
}}
QLabel#pathBar {{
    background-color: transparent;
    color: {TEXT_MUTED};
    font-size: 11px;
    padding: 4px 12px 4px 0;
    border: none;
}}
"""
    # the close-button rules go last: their image paths are runtime file
    # names resolved through `_asset`, not palette colours, so they must stay
    # out of the f-string above (the palette-token check scans its source)
    return qss + _close_button_qss(theme)


# The default (dark) application stylesheet, built lazily: `from theme import
# APP_QSS` (and `theme.APP_QSS`) still work, but a cold start does not pay for
# the big f-string unless the dark QSS is actually needed. `app_qss(theme)` is
# the normal entry point.
_APP_QSS_CACHE: dict[str, str] = {}


def __getattr__(name: str) -> str:
    if name == "APP_QSS":
        if DEFAULT_THEME not in _APP_QSS_CACHE:
            _APP_QSS_CACHE[DEFAULT_THEME] = app_qss(DEFAULT_THEME)
        return _APP_QSS_CACHE[DEFAULT_THEME]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
