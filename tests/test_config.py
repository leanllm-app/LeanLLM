from __future__ import annotations

import pytest

from leanllm import LeanLLMConfig
from leanllm.redaction import RedactionMode


def _scrub_env(monkeypatch):
    for var in (
        "LEANLLM_API_KEY",
        "LEANLLM_DATABASE_URL",
        "LEANLLM_ENDPOINT",
        "LEANLLM_ENABLE_PERSISTENCE",
        "LEANLLM_QUEUE_MAX_SIZE",
        "LEANLLM_BATCH_SIZE",
        "LEANLLM_FLUSH_INTERVAL_MS",
        "LEANLLM_AUTO_MIGRATE",
        "LEANLLM_CAPTURE_CONTENT",
        "LEANLLM_REDACTION_MODE",
        "LEANLLM_AUTO_NORMALIZE",
    ):
        monkeypatch.delenv(var, raising=False)


def test_defaults_match_documented_values():
    cfg = LeanLLMConfig()
    assert cfg.enable_persistence is True
    assert cfg.flush_interval_ms == 180_000
    assert cfg.batch_size == 100
    assert cfg.queue_max_size == 10_000
    assert cfg.auto_migrate is True
    assert cfg.capture_content is False
    assert cfg.auto_normalize is False
    assert cfg.redaction_mode == RedactionMode.METADATA_ONLY


def test_default_endpoint_is_leanllm_dev():
    cfg = LeanLLMConfig()
    assert cfg.endpoint == "https://api.leanllm.dev"


def test_from_env_reads_api_key_into_leanllm_api_key(monkeypatch):
    _scrub_env(monkeypatch)
    monkeypatch.setenv("LEANLLM_API_KEY", "lllm_xyz")
    cfg = LeanLLMConfig.from_env()
    assert cfg.leanllm_api_key == "lllm_xyz"


def test_from_env_reads_database_url(monkeypatch):
    _scrub_env(monkeypatch)
    monkeypatch.setenv("LEANLLM_DATABASE_URL", "sqlite:///./events.db")
    cfg = LeanLLMConfig.from_env()
    assert cfg.database_url == "sqlite:///./events.db"


def test_from_env_boolean_accepts_true_in_any_case(monkeypatch):
    _scrub_env(monkeypatch)
    monkeypatch.setenv("LEANLLM_API_KEY", "x")  # avoid mutex error
    for val in ("true", "True", "TRUE"):
        monkeypatch.setenv("LEANLLM_AUTO_NORMALIZE", val)
        cfg = LeanLLMConfig.from_env()
        assert cfg.auto_normalize is True


def test_from_env_boolean_non_true_strings_resolve_to_false(monkeypatch):
    _scrub_env(monkeypatch)
    monkeypatch.setenv("LEANLLM_API_KEY", "x")
    for val in ("yes", "1", "on", ""):
        monkeypatch.setenv("LEANLLM_AUTO_NORMALIZE", val)
        cfg = LeanLLMConfig.from_env()
        assert cfg.auto_normalize is False


def test_from_env_raises_when_both_database_url_and_api_key_set(monkeypatch):
    _scrub_env(monkeypatch)
    monkeypatch.setenv("LEANLLM_DATABASE_URL", "sqlite:///./events.db")
    monkeypatch.setenv("LEANLLM_API_KEY", "lllm_xyz")
    with pytest.raises(ValueError, match="mutually exclusive"):
        LeanLLMConfig.from_env()


def test_from_env_endpoint_override(monkeypatch):
    _scrub_env(monkeypatch)
    monkeypatch.setenv("LEANLLM_API_KEY", "x")
    monkeypatch.setenv("LEANLLM_ENDPOINT", "https://custom.test")
    cfg = LeanLLMConfig.from_env()
    assert cfg.endpoint == "https://custom.test"
