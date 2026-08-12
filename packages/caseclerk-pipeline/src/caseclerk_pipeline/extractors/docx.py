"""docx -> markdown extraction via mammoth."""

from __future__ import annotations

from pathlib import Path

import mammoth


def extract_docx(path: Path) -> str:
    with path.open("rb") as fh:
        result = mammoth.convert_to_markdown(fh)
    return str(result.value)
