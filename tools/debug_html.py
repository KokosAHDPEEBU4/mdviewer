import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import renderer

SOURCE = """Intro paragraph.

```python
def f(x):
    return x * 2
```

```
plain block
```

| a | b |
|---|---|
| 1 | 2 |

- [ ] open task
- [x] done task

Term
: definition

Footnote ref[^1]

[^1]: The footnote text.
"""

html, outline = renderer.render_with_outline(SOURCE)
for heading in outline:
    print(heading)
print()
print(html)

JSON_SOURCE = '{"name": "md", "count": 3, "ok": true, "items": [1, null]}'
print("\n=== JSON ===\n")
json_html, json_outline = renderer.render_json(JSON_SOURCE)
for heading in json_outline:
    print(heading)
print()
print(json_html)
