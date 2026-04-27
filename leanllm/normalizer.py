from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel


class InputType(str, Enum):
    CHAT = "chat"
    COMPLETION = "completion"
    TOOL = "tool"
    UNKNOWN = "unknown"


class OutputType(str, Enum):
    TEXT = "text"
    JSON = "json"
    CODE = "code"
    TOOL_CALL = "tool_call"
    UNKNOWN = "unknown"


class LengthBucket(str, Enum):
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


class NormalizedInput(BaseModel):
    input_type: InputType = InputType.UNKNOWN
    language: Optional[str] = None
    length_bucket: LengthBucket = LengthBucket.SHORT
    intent: Optional[str] = None
    semantic_hash: Optional[str] = None


class NormalizedOutput(BaseModel):
    output_type: OutputType = OutputType.UNKNOWN
    structure_detected: Optional[str] = None
    length_bucket: LengthBucket = LengthBucket.SHORT


# ----------------------------------------------------------------------
# Canonicalization
# ----------------------------------------------------------------------

_RE_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_RE_ISO_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?"
)
_RE_HEX_ID = re.compile(r"\b[0-9a-f]{16,}\b", re.IGNORECASE)
_RE_LONG_NUMBER = re.compile(r"\b\d{6,}\b")
_RE_WHITESPACE = re.compile(r"\s+")

_PLACEHOLDER_UUID = "<uuid>"
_PLACEHOLDER_TS = "<ts>"
_PLACEHOLDER_HEX = "<hex>"
_PLACEHOLDER_NUM = "<num>"


def canonicalize(*, text: str) -> str:
    """Return a canonical form: dynamic tokens masked, whitespace collapsed, lowercased."""
    text = _RE_UUID.sub(_PLACEHOLDER_UUID, text)
    text = _RE_ISO_TIMESTAMP.sub(_PLACEHOLDER_TS, text)
    text = _RE_HEX_ID.sub(_PLACEHOLDER_HEX, text)
    text = _RE_LONG_NUMBER.sub(_PLACEHOLDER_NUM, text)
    text = text.strip().lower()
    text = _RE_WHITESPACE.sub(" ", text)
    return text


def semantic_hash(*, text: str) -> str:
    """SHA-256 over the canonical form, truncated to 16 hex chars."""
    return hashlib.sha256(canonicalize(text=text).encode("utf-8")).hexdigest()[:16]


# ----------------------------------------------------------------------
# Classification
# ----------------------------------------------------------------------

_SHORT_MAX_WORDS = 50
_MEDIUM_MAX_WORDS = 500


def length_bucket(*, text: str) -> LengthBucket:
    word_count = len(text.split())
    if word_count <= _SHORT_MAX_WORDS:
        return LengthBucket.SHORT
    if word_count <= _MEDIUM_MAX_WORDS:
        return LengthBucket.MEDIUM
    return LengthBucket.LONG


_RE_LATIN = re.compile(r"[a-zA-ZÀ-ÿ]")
_RE_CJK = re.compile(r"[\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF]")
_RE_CYRILLIC = re.compile(r"[\u0400-\u04FF]")
_RE_ARABIC = re.compile(r"[\u0600-\u06FF]")


def detect_language(*, text: str) -> Optional[str]:
    """Return a coarse script tag: 'latin', 'cjk', 'cyrillic', 'arabic', or None."""
    if not text.strip():
        return None
    counts = {
        "latin": len(_RE_LATIN.findall(text)),
        "cjk": len(_RE_CJK.findall(text)),
        "cyrillic": len(_RE_CYRILLIC.findall(text)),
        "arabic": len(_RE_ARABIC.findall(text)),
    }
    winner = max(counts.items(), key=lambda kv: kv[1])
    if winner[1] == 0:
        return None
    return winner[0]


_RE_CODE_FENCE = re.compile(r"```")


def _looks_like_json(*, text: str) -> bool:
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return False
    try:
        json.loads(stripped)
        return True
    except (json.JSONDecodeError, ValueError):
        return False


def classify_output(*, text: str) -> Tuple[OutputType, Optional[str]]:
    """Return (output_type, structure_detected) for a response text."""
    if _looks_like_json(text=text):
        return OutputType.JSON, "json"
    if _RE_CODE_FENCE.search(text):
        return OutputType.CODE, "fenced_code"
    return OutputType.TEXT, None


def classify_input_type(*, messages: List[Dict[str, str]]) -> InputType:
    if not messages:
        return InputType.UNKNOWN
    roles = {m.get("role") for m in messages}
    if "tool" in roles:
        return InputType.TOOL
    return InputType.CHAT


# ----------------------------------------------------------------------
# Entry points
# ----------------------------------------------------------------------

def _extract_input_text(*, messages: List[Dict[str, str]]) -> str:
    parts: List[str] = []
    for m in messages:
        content = m.get("content")
        if content:
            parts.append(content)
    return " ".join(parts)


def normalize_input(
    *,
    messages: List[Dict[str, str]],
    auto_tag: bool = False,
) -> NormalizedInput:
    """Build a NormalizedInput from a chat messages list.

    `auto_tag=True` fills inferred fields (input_type, language); otherwise
    those remain at their enum defaults / None. `length_bucket` and
    `semantic_hash` are always populated when there is text.
    """
    text = _extract_input_text(messages=messages)
    bucket = length_bucket(text=text)
    sem_hash = semantic_hash(text=text) if text else None

    input_type = InputType.UNKNOWN
    language: Optional[str] = None
    if auto_tag:
        input_type = classify_input_type(messages=messages)
        language = detect_language(text=text)

    return NormalizedInput(
        input_type=input_type,
        language=language,
        length_bucket=bucket,
        intent=None,
        semantic_hash=sem_hash,
    )


def normalize_output(
    *,
    text: str,
    auto_tag: bool = False,
) -> NormalizedOutput:
    """Build a NormalizedOutput from a response text."""
    bucket = length_bucket(text=text)

    output_type = OutputType.UNKNOWN
    structure: Optional[str] = None
    if auto_tag:
        output_type, structure = classify_output(text=text)

    return NormalizedOutput(
        output_type=output_type,
        structure_detected=structure,
        length_bucket=bucket,
    )
