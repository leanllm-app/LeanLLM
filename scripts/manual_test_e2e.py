"""End-to-end test using a real LLM provider.

Requires:
    OPENAI_API_KEY=sk-...
    LEANLLM_DATABASE_URL=postgresql://leanllm:leanllm@localhost:5432/leanllm

Usage:
    python scripts/manual_test_e2e.py
"""
from __future__ import annotations

import logging
import os
import sys
import time

from leanllm import LeanLLM

logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("manual_test_e2e")


def main() -> int:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        log.error("Set OPENAI_API_KEY to run this test.")
        return 1
    if not os.getenv("LEANLLM_DATABASE_URL"):
        log.error("Set LEANLLM_DATABASE_URL to run this test.")
        return 1

    client = LeanLLM(api_key=api_key)

    log.info("Sending 3 chat requests with labels…")
    for i in range(3):
        response = client.chat(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": f"Reply with the number {i}."}],
            labels={"env": "e2e-test", "iteration": str(i)},
        )
        content = response.choices[0].message.content
        log.info("[%d] %s", i, content.strip())

    log.info("Waiting for worker to flush…")
    time.sleep(2.0)

    log.info("✓ E2E test sent. Check Postgres for rows where labels->>'env' = 'e2e-test'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
