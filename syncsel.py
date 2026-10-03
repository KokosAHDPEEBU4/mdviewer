"""Position map between the markdown source and the rendered preview.

The preview drops markup and rewrites tables/lists, so a selected character
range cannot be found verbatim in the other pane. Instead the two texts are
aligned by WORD anchors (runs of word characters, the same in both), and a
range is projected through those anchors. Pure Python: no Qt needed to test it.
"""

import re
from bisect import bisect_left, bisect_right

_WORD = re.compile(r"\w+", re.UNICODE)

# how far to look ahead when the two streams disagree (markup, captions, glyphs)
_SRC_LOOKAHEAD = 256
_REN_LOOKAHEAD = 64


def _words(text: str):
    return [(m.start(), m.end(), m.group()) for m in _WORD.finditer(text)]


def _lookahead(words, want, start, window):
    for k in range(start, min(len(words), start + window)):
        if words[k][2] == want:
            return k
    return None


def _align(source: str, render: str):
    """List of `(src_start, src_end, ren_start, ren_end)` for matched words."""
    source_words = _words(source)
    render_words = _words(render)
    spans = []
    i = j = 0
    while i < len(source_words) and j < len(render_words):
        if source_words[i][2] == render_words[j][2]:
            spans.append((source_words[i][0], source_words[i][1],
                          render_words[j][0], render_words[j][1]))
            i += 1
            j += 1
            continue
        src_ahead = _lookahead(source_words, render_words[j][2], i + 1, _SRC_LOOKAHEAD)
        ren_ahead = _lookahead(render_words, source_words[i][2], j + 1, _REN_LOOKAHEAD)
        if src_ahead is not None and (ren_ahead is None or (src_ahead - i) <= (ren_ahead - j)):
            i = src_ahead
        elif ren_ahead is not None:
            j = ren_ahead
        else:
            i += 1
            j += 1
    return spans


class SourceMap:
    """Maps a character range between the source and the rendered text."""

    def __init__(self, source: str, render: str):
        self._spans = _align(source, render)
        self._s0 = [span[0] for span in self._spans]
        self._s1 = [span[1] for span in self._spans]
        self._r0 = [span[2] for span in self._spans]
        self._r1 = [span[3] for span in self._spans]

    def __bool__(self) -> bool:
        return bool(self._spans)

    def to_render(self, start: int, end: int):
        """Source range -> rendered range, or None if nothing lines up."""
        return self._project(start, end, self._s0, self._s1, self._r0, self._r1)

    def to_source(self, start: int, end: int):
        """Rendered range -> source range, or None if nothing lines up."""
        return self._project(start, end, self._r0, self._r1, self._s0, self._s1)

    @staticmethod
    def _project(start, end, starts, ends, other_starts, other_ends):
        if start >= end or not starts:
            return None
        first = bisect_right(ends, start)          # first word with end > start
        last = bisect_left(starts, end) - 1        # last word with start < end
        if first > last or first >= len(starts):
            return None
        if start > starts[first]:
            other_start = min(other_starts[first] + (start - starts[first]),
                              other_ends[first] - 1)
        else:
            other_start = other_starts[first]
        if end <= ends[last]:
            other_end = other_starts[last] + (end - starts[last])
        else:
            other_end = other_ends[last]
        if other_end <= other_start:
            return None
        return (other_start, other_end)
