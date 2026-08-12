"""`python -m caseclerk_fixtures <dest>` -- build a synthetic Clio Drive at dest.

Dev-only entry point; not part of the MCP server's stdout-sensitive
stdio path, so plain print() here is fine.
"""

from __future__ import annotations

import sys
from pathlib import Path

from caseclerk_fixtures.generator import build_fixture_drive


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m caseclerk_fixtures <dest>", file=sys.stderr)
        return 2
    dest = build_fixture_drive(Path(args[0]))
    print(f"Built fixture Clio drive at {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
