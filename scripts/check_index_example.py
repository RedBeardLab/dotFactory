#!/usr/bin/env python3
"""Check that the landing page's anatomy block is the validated factory.

The page shows `examples/github/reply.fct` from the factory repository without
its leading comment banner. This script extracts the code block from
`index.html` and compares it, line for line, with that file. Run it after
checking out both repositories (see `.github/workflows/ci.yml`).
"""

from __future__ import annotations

import argparse
import difflib
import html
import re
import sys
from pathlib import Path


def page_lines(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    start = text.index('<div class="code" id="code"')
    end = text.index("\n          </div>\n        </div>", start)
    block = text[start:end]
    lines: list[str] = []
    for div in re.findall(r'<div class="cl"[^>]*>(.*?)</div>', block, re.S):
        plain = re.sub(r"<[^>]+>", "", div)
        plain = html.unescape(plain)
        plain = re.sub(r"^\s*\d+", "", plain, count=1)
        lines.append(plain.rstrip())
    return lines


def factory_lines(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = 0
    while start < len(lines) and (
        lines[start].startswith("#") or not lines[start].strip()
    ):
        start += 1
    return [line.rstrip() for line in lines[start:]]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("page", type=Path, help="index.html")
    parser.add_argument("factory", type=Path, help="examples/github/reply.fct")
    args = parser.parse_args()

    shown = page_lines(args.page)
    source = factory_lines(args.factory)
    if shown == source:
        print(f"ok: {len(shown)} page lines match {args.factory}")
        return 0

    diff = difflib.unified_diff(
        source,
        shown,
        fromfile=str(args.factory),
        tofile=str(args.page),
        lineterm="",
    )
    print("the page's anatomy block has drifted from the validated factory:")
    print("\n".join(diff))
    return 1


if __name__ == "__main__":
    sys.exit(main())
