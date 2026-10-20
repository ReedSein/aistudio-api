"""Documentation must not contain characters that render as nothing."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INVISIBLE = {
    "\u200b": "ZERO WIDTH SPACE",
    "\u200c": "ZERO WIDTH NON-JOINER",
    "\u200d": "ZERO WIDTH JOINER",
    "\u2060": "WORD JOINER",
    "\ufeff": "ZERO WIDTH NO-BREAK SPACE / BOM",
}
DOCS = sorted(
    [
        *ROOT.glob("docs/*.md"),
        ROOT / "README.md",
        ROOT / "README_EN.md",
        ROOT / "AGENT.md",
    ]
)


@pytest.mark.parametrize("path", DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_doc_has_no_invisible_characters(path: Path):
    # Write such characters as visible escapes (for example "\\u200b") instead.
    found = [
        f"{path.relative_to(ROOT)}:{number}: {name}"
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        for char, name in INVISIBLE.items()
        if char in line
    ]
    assert not found, "\n".join(found)
