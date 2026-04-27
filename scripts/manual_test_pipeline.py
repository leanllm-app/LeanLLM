"""End-to-end test of the event pipeline without hitting any LLM provider.

Pushes N synthetic LLMEvents through the queue → worker → Postgres path
and verifies they land correctly.

Usage:
    LEANLLM_DATABASE_URL=postgresql://leanllm:leanllm@localhost:5432/leanllm \\
        python scripts/manual_test_pipeline.py [N]
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import sys
import time
import uuid
from datetime import datetime, timezone

from leanllm.config import LeanLLMConfig
from leanllm.events.models import LLMEvent
from leanllm.events.queue import EventQueue
from leanllm.events.worker import EventWorker
from leanllm.storage import create_store

logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("manual_test")


def make_event(i: int) -> LLMEvent:
    return LLMEvent(
        event_id=str(uuid.uuid4()),
        timestamp=datetime.now(timezone.utc),
        model=random.choice(["gpt-4o-mini", "gpt-4o", "claude-3-5-sonnet-20241022"]),
        provider=random.choice(["openai", "anthropic"]),
        input_tokens=random.randint(50, 500),
        output_tokens=random.randint(20, 300),
        total_tokens=0,  # filled below
        cost=round(random.uniform(0.0001, 0.05), 6),
        latency_ms=random.randint(100, 2000),
        labels={
            "team": random.choice(["backend", "frontend", "data"]),
            "feature": random.choice(["onboarding", "search", "chat"]),
            "env": "manual-test",
        },
        metadata={"finish_reason": "stop", "iteration": i},
    )


async def verify_count(*, url: str, expected: int) -> int:
    from urllib.parse import urlparse

    scheme = urlparse(url).scheme.lower()

    if scheme.startswith("postgres"):
        import asyncpg
        conn = await asyncpg.connect(url)
        try:
            n = await conn.fetchval(
                "SELECT count(*) FROM llm_events WHERE labels->>'env' = 'manual-test'"
            )
            return int(n or 0)
        finally:
            await conn.close()

    if scheme == "sqlite":
        import aiosqlite
        from leanllm.storage.sqlite import _path_from_url
        async with aiosqlite.connect(_path_from_url(url)) as conn:
            cur = await conn.execute(
                "SELECT count(*) FROM llm_events "
                "WHERE json_extract(labels, '$.env') = 'manual-test'"
            )
            row = await cur.fetchone()
            return int(row[0] if row else 0)

    raise ValueError(f"Unsupported scheme: {scheme}")


async def main(n: int) -> int:
    cfg = LeanLLMConfig.from_env()
    if not cfg.database_url:
        log.error("Set LEANLLM_DATABASE_URL before running.")
        return 1

    store = create_store(url=cfg.database_url, auto_migrate=cfg.auto_migrate)
    queue = EventQueue(max_size=max(n * 2, 1000))
    worker = EventWorker(
        queue=queue,
        store=store,
        batch_size=min(n, cfg.batch_size),
        flush_interval_ms=cfg.flush_interval_ms,
    )

    log.info("Starting worker, then enqueueing %d synthetic events…", n)
    worker.start()

    enqueued = 0
    start = time.perf_counter()
    for i in range(n):
        ev = make_event(i)
        ev.total_tokens = ev.input_tokens + ev.output_tokens
        if queue.enqueue(ev):
            enqueued += 1
    elapsed_ms = (time.perf_counter() - start) * 1000
    log.info("Enqueued %d/%d events in %.2f ms (%.0f events/sec)",
             enqueued, n, elapsed_ms, enqueued / max(elapsed_ms / 1000, 1e-6))

    log.info("Waiting for worker to drain…")
    while not queue.empty():
        await asyncio.sleep(0.1)
    # Give the worker one more flush cycle
    await asyncio.sleep(max(cfg.flush_interval_ms / 1000 * 2, 0.5))

    log.info("Stopping worker (graceful drain)…")
    worker.stop(timeout=5.0)

    persisted = await verify_count(url=cfg.database_url, expected=enqueued)
    log.info("Persisted rows tagged env=manual-test: %d (expected ≥ %d)",
             persisted, enqueued)

    if persisted >= enqueued:
        log.info("✓ Pipeline test passed.")
        return 0
    log.error("✗ Pipeline test FAILED — %d events lost.", enqueued - persisted)
    return 1


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    sys.exit(asyncio.run(main(n)))
