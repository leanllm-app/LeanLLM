"""Module 13 — CLI logs / show / replay against a SQLite-backed fixture."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import List

import pytest

from leanllm import LLMEvent
from leanllm.cli import main
from leanllm.cli._store import parse_when
from leanllm.events.models import ErrorKind
from leanllm.storage.sqlite import SQLiteEventStore


# ----------------------------------------------------------------------
# Fixtures + helpers (all synchronous; CLI does its own asyncio.run)
# ----------------------------------------------------------------------


def _ev(
    *,
    event_id: str,
    correlation_id: str | None = None,
    model: str = "gpt-4o-mini",
    error_kind: ErrorKind | None = None,
    timestamp: datetime | None = None,
) -> LLMEvent:
    return LLMEvent(
        event_id=event_id,
        timestamp=timestamp or datetime.now(timezone.utc),
        correlation_id=correlation_id,
        model=model,
        provider="openai",
        input_tokens=5,
        output_tokens=10,
        total_tokens=15,
        cost=0.001,
        latency_ms=42,
        prompt=json.dumps([{"role": "user", "content": "hi"}]),
        response="hello",
        parameters={"temperature": 0.5},
        error_kind=error_kind,
        error_message="boom" if error_kind else None,
    )


def _seed_db(path: str, events: List[LLMEvent]) -> None:
    async def go():
        store = SQLiteEventStore(database_url=f"sqlite:///{path}")
        await store.initialize()
        await store.save_batch(events)
        await store.close()

    asyncio.run(go())


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "events.db")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip LEANLLM env vars so tests are deterministic."""
    for k in (
        "LEANLLM_DATABASE_URL",
        "LEANLLM_API_KEY",
        "LEANLLM_ENDPOINT",
        "LEANLLM_REDACTION_MODE",
    ):
        monkeypatch.delenv(k, raising=False)


# ----------------------------------------------------------------------
# parse_when helper
# ----------------------------------------------------------------------


def test_parse_when_iso_date():
    out = parse_when("2026-04-27")
    assert out.year == 2026 and out.month == 4 and out.day == 27
    assert out.tzinfo is not None


def test_parse_when_iso_datetime_with_tz():
    out = parse_when("2026-04-27T10:00:00+00:00")
    assert out.tzinfo is not None
    assert out.hour == 10


def test_parse_when_naive_iso_treated_as_utc():
    out = parse_when("2026-04-27T10:00:00")
    assert out.tzinfo is not None


def test_parse_when_relative_hours():
    before = datetime.now(timezone.utc) - timedelta(hours=1)
    out = parse_when("1h")
    delta = abs((out - before).total_seconds())
    assert delta < 5  # within 5s of expected


def test_parse_when_relative_minutes():
    out = parse_when("30m")
    expected = datetime.now(timezone.utc) - timedelta(minutes=30)
    assert abs((out - expected).total_seconds()) < 5


def test_parse_when_relative_days():
    out = parse_when("2d")
    expected = datetime.now(timezone.utc) - timedelta(days=2)
    assert abs((out - expected).total_seconds()) < 5


def test_parse_when_empty_raises():
    with pytest.raises(ValueError):
        parse_when("")


# ----------------------------------------------------------------------
# leanllm logs
# ----------------------------------------------------------------------


def test_logs_table_default_renders_header_and_event(db_path, capsys):
    _seed_db(db_path, [_ev(event_id="evt-table-1")])
    rc = main(["logs", "--url", f"sqlite:///{db_path}"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "event_id" in captured.out  # header
    assert "evt-table-1" in captured.out


def test_logs_json_format_outputs_jsonl(db_path, capsys):
    _seed_db(db_path, [_ev(event_id="j1"), _ev(event_id="j2")])
    rc = main(["logs", "--url", f"sqlite:///{db_path}", "--format", "json"])
    captured = capsys.readouterr()
    assert rc == 0
    lines = [line for line in captured.out.strip().split("\n") if line]
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert {p["event_id"] for p in parsed} == {"j1", "j2"}


def test_logs_filters_by_correlation_id(db_path, capsys):
    _seed_db(
        db_path,
        [
            _ev(event_id="a", correlation_id="C1"),
            _ev(event_id="b", correlation_id="C2"),
        ],
    )
    rc = main(
        [
            "logs",
            "--url",
            f"sqlite:///{db_path}",
            "--correlation-id",
            "C1",
            "--format",
            "json",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 0
    lines = captured.out.strip().split("\n")
    assert len(lines) == 1
    assert json.loads(lines[0])["event_id"] == "a"


def test_logs_filters_by_model(db_path, capsys):
    _seed_db(
        db_path,
        [
            _ev(event_id="a", model="gpt-4o-mini"),
            _ev(event_id="b", model="gpt-4o"),
        ],
    )
    rc = main(
        [
            "logs",
            "--url",
            f"sqlite:///{db_path}",
            "--model",
            "gpt-4o",
            "--format",
            "json",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 0
    lines = captured.out.strip().split("\n")
    assert len(lines) == 1
    assert json.loads(lines[0])["event_id"] == "b"


def test_logs_errors_only(db_path, capsys):
    _seed_db(
        db_path,
        [
            _ev(event_id="ok"),
            _ev(event_id="err", error_kind=ErrorKind.TIMEOUT),
        ],
    )
    rc = main(
        [
            "logs",
            "--url",
            f"sqlite:///{db_path}",
            "--errors-only",
            "--format",
            "json",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 0
    lines = captured.out.strip().split("\n")
    assert len(lines) == 1
    assert json.loads(lines[0])["event_id"] == "err"


def test_logs_limit_and_offset(db_path, capsys):
    base = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)
    _seed_db(
        db_path,
        [
            _ev(event_id=f"e{i}", timestamp=base + timedelta(minutes=i))
            for i in range(5)
        ],
    )
    rc = main(
        [
            "logs",
            "--url",
            f"sqlite:///{db_path}",
            "--limit",
            "2",
            "--offset",
            "1",
            "--format",
            "json",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 0
    lines = captured.out.strip().split("\n")
    ids = [json.loads(line)["event_id"] for line in lines]
    # ordered DESC by timestamp; offset=1 skips e4, returns e3, e2
    assert ids == ["e3", "e2"]


def test_logs_empty_store_renders_only_header(db_path, capsys):
    _seed_db(db_path, [])
    rc = main(["logs", "--url", f"sqlite:///{db_path}"])
    captured = capsys.readouterr()
    assert rc == 0
    # table format always prints header + separator
    assert "event_id" in captured.out


def test_logs_aborts_when_only_api_key_set(monkeypatch, capsys):
    monkeypatch.setenv("LEANLLM_API_KEY", "lllm_xxx")
    with pytest.raises(SystemExit) as exc:
        main(["logs"])
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "LEANLLM_DATABASE_URL" in captured.err
    assert "SaaS" in captured.err


# ----------------------------------------------------------------------
# leanllm show
# ----------------------------------------------------------------------


def test_show_prints_full_json_by_default(db_path, capsys):
    _seed_db(db_path, [_ev(event_id="show-1")])
    rc = main(["show", "show-1", "--url", f"sqlite:///{db_path}"])
    captured = capsys.readouterr()
    assert rc == 0
    parsed = json.loads(captured.out)
    assert parsed["event_id"] == "show-1"
    assert parsed["model"] == "gpt-4o-mini"


def test_show_pretty_renders_sectioned_view(db_path, capsys):
    _seed_db(db_path, [_ev(event_id="show-2")])
    rc = main(["show", "show-2", "--url", f"sqlite:///{db_path}", "--pretty"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "Event show-2" in captured.out
    assert "tokens:" in captured.out


def test_show_not_found_exits_1(db_path, capsys):
    _seed_db(db_path, [])
    rc = main(["show", "nope", "--url", f"sqlite:///{db_path}"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "not found" in captured.err


# ----------------------------------------------------------------------
# leanllm replay
# ----------------------------------------------------------------------


def test_replay_single_runs_through_engine(db_path, capsys, monkeypatch):
    _seed_db(db_path, [_ev(event_id="r1")])

    def fake_chat(**kw):
        message = SimpleNamespace(content="new response", tool_calls=None)
        choice = SimpleNamespace(message=message, finish_reason="stop")
        usage = SimpleNamespace(prompt_tokens=1, completion_tokens=2, total_tokens=3)
        return SimpleNamespace(choices=[choice], usage=usage)

    monkeypatch.setattr("leanllm.client.chat_completion", fake_chat)
    rc = main(["replay", "r1", "--url", f"sqlite:///{db_path}"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "replay r1" in captured.out  # one-line summary


def test_replay_print_diff_uses_pretty_print(db_path, capsys, monkeypatch):
    _seed_db(db_path, [_ev(event_id="r2")])

    def fake_chat(**kw):
        message = SimpleNamespace(content="completely different", tool_calls=None)
        choice = SimpleNamespace(message=message, finish_reason="stop")
        usage = SimpleNamespace(prompt_tokens=1, completion_tokens=2, total_tokens=3)
        return SimpleNamespace(choices=[choice], usage=usage)

    monkeypatch.setattr("leanllm.client.chat_completion", fake_chat)
    rc = main(
        [
            "replay",
            "r2",
            "--url",
            f"sqlite:///{db_path}",
            "--print-diff",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert "Replay r2" in captured.out
    assert "── diff ──" in captured.out


def test_replay_not_found_exits_1(db_path, capsys, monkeypatch):
    _seed_db(db_path, [])
    rc = main(["replay", "missing", "--url", f"sqlite:///{db_path}"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "not found" in captured.err


def test_replay_model_override_passes_through(db_path, capsys, monkeypatch):
    _seed_db(db_path, [_ev(event_id="r3", model="gpt-4o-mini")])
    seen_kwargs: dict = {}

    def fake_chat(**kw):
        seen_kwargs.update(kw)
        message = SimpleNamespace(content="ok", tool_calls=None)
        choice = SimpleNamespace(message=message, finish_reason="stop")
        usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
        return SimpleNamespace(choices=[choice], usage=usage)

    monkeypatch.setattr("leanllm.client.chat_completion", fake_chat)
    rc = main(
        [
            "replay",
            "r3",
            "--url",
            f"sqlite:///{db_path}",
            "--model",
            "gpt-4o",
            "--temperature",
            "0.0",
        ]
    )
    capsys.readouterr()
    assert rc == 0
    assert seen_kwargs["model"] == "gpt-4o"
    assert seen_kwargs.get("temperature") == 0.0


def test_replay_batch_aggregates_summary(db_path, capsys, monkeypatch, tmp_path):
    _seed_db(db_path, [_ev(event_id="b1"), _ev(event_id="b2"), _ev(event_id="b3")])

    def fake_chat(**kw):
        message = SimpleNamespace(content="hello", tool_calls=None)
        choice = SimpleNamespace(message=message, finish_reason="stop")
        usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
        return SimpleNamespace(choices=[choice], usage=usage)

    monkeypatch.setattr("leanllm.client.chat_completion", fake_chat)
    batch_file = tmp_path / "ids.txt"
    batch_file.write_text("# comment\nb1\n\nb2\nb3\n")

    rc = main(
        [
            "replay",
            "--batch",
            str(batch_file),
            "--url",
            f"sqlite:///{db_path}",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert "Batch summary" in captured.out
    assert "replays=3" in captured.out
    assert "errors=0" in captured.out


def test_replay_batch_warns_on_missing_ids(db_path, capsys, monkeypatch, tmp_path):
    _seed_db(db_path, [_ev(event_id="exists")])

    def fake_chat(**kw):
        message = SimpleNamespace(content="x", tool_calls=None)
        choice = SimpleNamespace(message=message, finish_reason="stop")
        usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
        return SimpleNamespace(choices=[choice], usage=usage)

    monkeypatch.setattr("leanllm.client.chat_completion", fake_chat)
    batch_file = tmp_path / "ids.txt"
    batch_file.write_text("exists\nmissing\n")

    rc = main(
        [
            "replay",
            "--batch",
            str(batch_file),
            "--url",
            f"sqlite:///{db_path}",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert "1 event(s) not found" in captured.err
    assert "replays=1" in captured.out


def test_replay_no_event_id_and_no_batch_exits_2(db_path, capsys):
    _seed_db(db_path, [])
    rc = main(["replay", "--url", f"sqlite:///{db_path}"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "event_id" in captured.err
