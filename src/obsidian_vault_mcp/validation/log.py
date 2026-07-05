"""Violation log (M2 Frontmatter Governance Spec §7).

Every warn-mode violation and every block-mode rejection is appended to a
JSONL file. This feeds the L3 periodic lint. Writing the log must never break
a vault write, so all failures here are swallowed (and logged to stderr).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def append_violation(
    log_file: Path,
    *,
    tool: str,
    path: str,
    codes: list[str],
    mode: str,
    written: bool,
) -> None:
    """Append one JSONL record. Best-effort: never raises."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "path": path,
        "codes": codes,
        "mode": mode,
        "written": written,
    }
    try:
        log_file = Path(log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:  # noqa: BLE001 -- logging must not break writes
        logger.error("L2 validator: failed to append violation log to %s: %s", log_file, e)
