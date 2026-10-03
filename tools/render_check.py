"""Render smoke test: renders a sample document and checks that the dark theme
actually paints (Qt silently ignores most of CSS, so eyeballing is not enough).

    python tools/render_check.py [out.png]

Scans the rendered image for the palette colours the theme is supposed to use,
always writes the PNG for a human to look at, and exits non-zero when one of
them is missing.
"""

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from main import create_app  # noqa: E402
from renderer import render_with_outline  # noqa: E402
from theme import ACCENT, BG, CODE_BG, CODE_INLINE_BG, SUCCESS, SURFACE  # noqa: E402
from viewer import Viewer  # noqa: E402

SAMPLE = """\
# MD Viewer

Абзац с **жирным**, *курсивом*, `инлайн-кодом` и [ссылкой](https://example.com).
Строка подлиннее, чтобы проверить перенос: Lorem ipsum dolor sit amet, consectetur
adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.

## Списки и задачи

- пункт один
- пункт два
  - вложенный пункт
- [x] сделанная задача
- [ ] невыполненная задача

## Код

```python
def hello(name: str) -> str:
    return f"привет, {name}"  # достаточно длинная строка для проверки переноса
```

## Таблица

| Колонка | Значение | Описание |
|---|---|---|
| alpha | 1 | первая |
| beta | 2 | вторая |

## Цитата

> Кавычка с **форматированием** и `кодом`.

---

Сноска[^note].

[^note]: Текст сноски.
"""

EXPECTED = {
    "page background": (BG, 4000),
    "link accent": (ACCENT, 10),
    "code card": (CODE_BG, 2000),
    "inline code": (CODE_INLINE_BG, 300),
    "quote background": (SURFACE, 1500),
    "checked task box": (SUCCESS, 8),
}

TOLERANCE = 14


def _closest_counts(image: QImage, colour: str) -> int:
    want = QColor(colour)
    hits = 0
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            got = image.pixelColor(x, y)
            if (
                abs(got.red() - want.red())
                + abs(got.green() - want.green())
                + abs(got.blue() - want.blue())
            ) <= TOLERANCE:
                hits += 1
    return hits


def _place_off_primary(widget) -> None:
    """Optionally move the window off the primary monitor for a run.

    `MDVIEWER_TEST_SCREEN=secondary` (or a screen name) places it there;
    otherwise the window is left where Qt puts it.
    """
    want = os.environ.get("MDVIEWER_TEST_SCREEN", "").strip().lower()
    if not want:
        return
    screens = QApplication.screens()
    if want == "secondary":
        target = next((s for s in screens if s is not QApplication.primaryScreen()), None)
    else:
        target = next((s for s in screens if s.name().lower() == want), None)
    if target is None:
        return
    geo = target.availableGeometry()
    size = widget.size()
    widget.setGeometry(geo.x() + 20, geo.y() + 20, size.width(), size.height())


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.gettempdir()) / "mdviewer.png"
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "sample.md"
        source.write_text(SAMPLE, encoding="utf-8")

        app = create_app()
        viewer = Viewer(source)
        viewer.resize(1000, 1500)
        _place_off_primary(viewer)
        viewer.show()
        app.processEvents()
        image = viewer.grab().toImage()

    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(str(out))

    failures = []
    for name, (colour, minimum) in EXPECTED.items():
        hits = _closest_counts(image, colour)
        ok = hits >= minimum
        print(f"{'ok  ' if ok else 'FAIL'} {name:<18} {colour} pixels={hits} (need {minimum})")
        if not ok:
            failures.append(name)

    failures += _check_outline_budget()

    print(f"\npng: {out}")
    return 1 if failures else 0


def _check_outline_budget() -> list[str]:
    """The outline must not slow opening down: anchors and the outline come
    from the same pass as the render, and the y-offsets are measured lazily."""
    body = "# Doc\n\n" + "\n\n".join(
        f"## Section {i}\n\n" + "text line\n" * 8 for i in range(500)
    )
    start = time.perf_counter()
    html, outline = render_with_outline(body)
    elapsed = (time.perf_counter() - start) * 1000
    failures = []
    ok = elapsed < 150
    print(f"{'ok  ' if ok else 'FAIL'} {'outline budget':<18} {elapsed:.1f} ms for {len(outline)} headings")
    if not ok:
        failures.append("outline budget")
    if outline and html.count('<a name="md-h') != len(outline):
        print(f"FAIL {'outline anchors':<18} {html.count('<a name=')} anchors for {len(outline)} headings")
        failures.append("outline anchors")
    else:
        print(f"ok   {'outline anchors':<18} all {len(outline)} headings anchored")
    failures += _check_table(render_with_outline)
    return failures


def _check_table(renderer) -> list[str]:
    """Tables need visible borders and alternating row colours, which Qt gets
    from HTML attributes (it ignores :nth-child and many CSS borders)."""
    html, _ = renderer("| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n")
    failures = []
    checks = {
        "table borders": 'border="1"' in html,
        "table zebra rows": html.count("bgcolor=") >= 3,  # header + two body rows
    }
    for name, ok in checks.items():
        print(f"{'ok  ' if ok else 'FAIL'} {name:<18} {ok}")
        if not ok:
            failures.append(name)
    return failures


if __name__ == "__main__":
    sys.exit(main())