from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

LOG_FILE: str = os.getenv("LEANLLM_LOG_FILE", "llm_logs.json")
LOG_DIR: Path = Path(os.getenv("LEANLLM_LOG_DIR", "."))
