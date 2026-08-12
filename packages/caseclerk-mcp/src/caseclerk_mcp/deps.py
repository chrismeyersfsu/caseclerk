"""Shared dependencies threaded into every tool/prompt registration function.

Every tool opens its own short-lived sqlite3 connection (WAL + busy_timeout
make that cheap and safe alongside other CaseClerk processes touching the
same db) rather than sharing one connection across threads.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from caseclerk_core import db
from caseclerk_core.config import Config
from caseclerk_core.paths import safe_join


@dataclass(frozen=True)
class Deps:
    config: Config
    documents_root: Path | None
    prompts_dir: Path
    db_path: Path | None = None

    def open_db(self) -> sqlite3.Connection:
        return db.connect(self.db_path)

    def case_directory(self, client: str, case_number: str) -> Path:
        if self.documents_root is None:
            raise ValueError("documentsRoot is not configured; run `caseclerk init` first")
        return safe_join(self.documents_root, client, case_number)
