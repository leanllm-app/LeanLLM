from __future__ import annotations

import os
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel

from .redaction import RedactionMode

load_dotenv()


class LeanLLMConfig(BaseModel):
    # Persistence — mutually exclusive
    database_url: Optional[str] = None
    leanllm_api_key: Optional[str] = None
    endpoint: str = "https://api.leanllm.dev"

    enable_persistence: bool = True

    # Queue
    queue_max_size: int = 10_000

    # Worker flush policy: whichever triggers first
    batch_size: int = 100
    flush_interval_ms: int = 180_000  # 3 minutes

    # Auto-run pending migrations on store init (Postgres only)
    auto_migrate: bool = True

    # Privacy: set True to store prompt/response text
    capture_content: bool = False

    # Redaction mode for stored content
    redaction_mode: RedactionMode = RedactionMode.METADATA_ONLY

    # Semantic normalization: populate LLMEvent.normalized_input / normalized_output
    auto_normalize: bool = False

    # Module 14 — per-process tuning
    sampling_rate: float = 1.0  # 1.0 = log everything; 0.0 = log nothing
    environment: Optional[str] = None  # mirrored into LLMEvent.metadata["environment"]
    debug: bool = False  # DEBUG log level + per-event stderr summary

    # Module 16 — DX
    last_event_buffer: int = (
        32  # in-memory ring buffer for client.last_event / .recent_events; 0 disables
    )
    auto_chain: bool = False  # auto-fill parent_request_id with the last event_id in the same async task

    # Module 15 — resilient delivery (in-memory; no disk fallback by design)
    retry_max_attempts: int = 5  # number of save_batch attempts before dropping a batch
    retry_initial_backoff_ms: int = (
        500  # base backoff between attempts; doubles each time, ±20% jitter
    )
    retry_total_budget_ms: int = 30_000  # cap on total retry time per batch — drop early if next sleep would exceed

    @classmethod
    def from_env(cls) -> "LeanLLMConfig":
        database_url = os.getenv("LEANLLM_DATABASE_URL")
        api_key = os.getenv("LEANLLM_API_KEY")

        if database_url and api_key:
            raise ValueError(
                "LEANLLM_DATABASE_URL and LEANLLM_API_KEY are mutually exclusive. "
                "Set one or the other, not both."
            )

        redaction_mode_str = os.getenv("LEANLLM_REDACTION_MODE", "metadata").lower()
        try:
            redaction_mode = RedactionMode(redaction_mode_str)
        except ValueError:
            redaction_mode = RedactionMode.METADATA_ONLY

        return cls(
            database_url=database_url,
            leanllm_api_key=api_key,
            endpoint=os.getenv("LEANLLM_ENDPOINT", "https://api.leanllm.dev"),
            enable_persistence=os.getenv("LEANLLM_ENABLE_PERSISTENCE", "true").lower()
            == "true",
            queue_max_size=int(os.getenv("LEANLLM_QUEUE_MAX_SIZE", "10000")),
            batch_size=int(os.getenv("LEANLLM_BATCH_SIZE", "100")),
            flush_interval_ms=int(os.getenv("LEANLLM_FLUSH_INTERVAL_MS", "180000")),
            auto_migrate=os.getenv("LEANLLM_AUTO_MIGRATE", "true").lower() == "true",
            capture_content=os.getenv("LEANLLM_CAPTURE_CONTENT", "false").lower()
            == "true",
            redaction_mode=redaction_mode,
            auto_normalize=os.getenv("LEANLLM_AUTO_NORMALIZE", "false").lower()
            == "true",
            sampling_rate=float(os.getenv("LEANLLM_SAMPLING_RATE", "1.0")),
            environment=os.getenv("LEANLLM_ENVIRONMENT"),
            debug=os.getenv("LEANLLM_DEBUG", "false").lower() == "true",
            last_event_buffer=int(os.getenv("LEANLLM_LAST_EVENT_BUFFER", "32")),
            auto_chain=os.getenv("LEANLLM_AUTO_CHAIN", "false").lower() == "true",
            retry_max_attempts=int(os.getenv("LEANLLM_RETRY_MAX_ATTEMPTS", "5")),
            retry_initial_backoff_ms=int(
                os.getenv("LEANLLM_RETRY_INITIAL_BACKOFF_MS", "500")
            ),
            retry_total_budget_ms=int(
                os.getenv("LEANLLM_RETRY_TOTAL_BUDGET_MS", "30000")
            ),
        )
