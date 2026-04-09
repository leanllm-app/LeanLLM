from __future__ import annotations

import json
from pathlib import Path

from .config import LOG_DIR, LOG_FILE
from .types import UsageEvent


def track_event(event: UsageEvent) -> None:
    """Log a usage event to console and append it to the JSON log file."""
    payload = event.model_dump()
    print(f"[LeanLLM] {json.dumps(payload)}")

    log_path = Path(LOG_DIR) / LOG_FILE
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with open(log_path, "a") as f:
        f.write(json.dumps(payload) + "\n")
