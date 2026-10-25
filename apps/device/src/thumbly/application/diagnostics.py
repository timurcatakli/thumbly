"""Consented diagnostics export (PRD FR-10).

Included: software versions, configuration without secrets, the same status the owner sees,
network state without credentials, journal counts and the recent operation list, load, and a
bounded, redacted excerpt of the service log. Excluded by construction: images, typed text,
credentials, pairing records, raw USB dumps.
"""

import platform
import sys
from collections import deque
from pathlib import Path
from typing import Any

from thumbly import __version__
from thumbly.interfaces.app_api import redact

LOG_LINES = 400
LOG = Path.home() / "Library" / "Logs" / "Thumbly" / "thumbly.log"


async def log_excerpt(path: Path = LOG) -> list[str]:
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            lines = deque(handle, LOG_LINES)
    except OSError:
        return ["<log unavailable>"]
    return [redact(line.rstrip("\n")) for line in lines]


async def export(service) -> dict[str, Any]:
    config = service.config
    settings = {k: str(v) if k.endswith("_dir") else v for k, v in config.model_dump().items()}
    status = await service.phone.status()
    counts = {
        row["state"]: row["n"]
        for row in service.journal.db.execute(
            "SELECT state, COUNT(*) AS n FROM operations GROUP BY state"
        ).fetchall()
    }
    result: dict[str, Any] = {
        "versions": {
            "thumbly": __version__,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "config": settings,
        "status": status,
        "journal": {"counts": counts, "recent": service.journal.list_operations(100)},
        "logs": await log_excerpt(),
        "network": await service.host.status(),
    }
    return result
