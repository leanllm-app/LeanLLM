from __future__ import annotations

import json

import httpx
import pytest

from leanllm import LLMEvent
from leanllm.storage.remote import RemoteEventStore


def _ev(eid: str = "remote-1") -> LLMEvent:
    return LLMEvent(
        event_id=eid,
        model="gpt-4o-mini",
        provider="openai",
        input_tokens=1,
        output_tokens=2,
        total_tokens=3,
        cost=0.0,
        latency_ms=0,
    )


def _install_transport(
    *, store: RemoteEventStore, transport: httpx.MockTransport
) -> None:
    """Replace the live AsyncClient with one driven by a MockTransport."""
    store._client = httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(5.0),
        headers={
            "Authorization": f"Bearer {store._api_key}",
            "Content-Type": "application/json",
        },
    )


async def test_save_batch_posts_v1_events_with_bearer_and_body():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"accepted": 1, "dropped": 0})

    store = RemoteEventStore(api_key="lllm_xyz", endpoint="https://example.test")
    _install_transport(store=store, transport=httpx.MockTransport(handler))

    await store.save_batch([_ev("e1")])

    assert captured["url"] == "https://example.test/v1/events"
    assert captured["method"] == "POST"
    assert captured["auth"] == "Bearer lllm_xyz"
    assert "events" in captured["body"]
    assert captured["body"]["events"][0]["event_id"] == "e1"
    await store.close()


async def test_save_batch_empty_or_uninitialized_is_noop():
    store = RemoteEventStore(api_key="lllm_x", endpoint="https://example.test")
    # No transport installed; should not raise
    await store.save_batch([_ev()])
    await store.save_batch([])

    captured = {"calls": 0}

    def handler(request):
        captured["calls"] += 1
        return httpx.Response(200, json={"accepted": 0, "dropped": 0})

    _install_transport(store=store, transport=httpx.MockTransport(handler))
    await store.save_batch([])
    assert captured["calls"] == 0
    await store.close()


async def test_save_batch_warns_when_service_drops(caplog):
    def handler(request):
        return httpx.Response(200, json={"accepted": 0, "dropped": 1})

    store = RemoteEventStore(api_key="lllm_x", endpoint="https://example.test")
    _install_transport(store=store, transport=httpx.MockTransport(handler))
    with caplog.at_level("WARNING"):
        await store.save_batch([_ev()])
    assert any("dropped 1 events" in m for m in caplog.messages)
    await store.close()


async def test_save_batch_raises_on_http_error_status():
    def handler(request):
        return httpx.Response(500, json={"error": "server"})

    store = RemoteEventStore(api_key="lllm_x", endpoint="https://example.test")
    _install_transport(store=store, transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        await store.save_batch([_ev()])
    await store.close()
