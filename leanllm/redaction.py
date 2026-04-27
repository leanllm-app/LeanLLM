from __future__ import annotations

import re
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class RedactionMode(str, Enum):
    FULL = "full"                # store prompt + response as-is
    REDACTED = "redacted"        # apply masking rules before storing
    METADATA_ONLY = "metadata"   # never store prompt/response


class RedactionPolicy(BaseModel):
    """
    Declarative redaction policy.

    Defines the shape only. Actual masking (built-in patterns for email/phone/IDs
    and custom regex evaluation) is implemented by module 9 (Privacy & Redaction).
    """

    mode: RedactionMode = RedactionMode.METADATA_ONLY

    redact_emails: bool = True
    redact_phones: bool = True
    redact_ids: bool = True          # CPF / SSN style patterns

    custom_patterns: List[str] = Field(default_factory=list)

    exclude_prompt: bool = False
    exclude_response: bool = False


# Built-in redaction patterns (compiled at module load time)
_PATTERNS = {
    "email": re.compile(
        r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", re.IGNORECASE
    ),
    "phone": re.compile(
        r"(?:\+?55\s?)?(?:\(?[0-9]{2}\)?\s?)?(?:9)?[0-9]{4}[-.\s]?[0-9]{4}(?:-?[0-9]{2})?"
    ),
    "cpf": re.compile(r"\b[0-9]{3}\.?[0-9]{3}\.?[0-9]{3}-?[0-9]{2}\b"),
    "ssn": re.compile(r"\b[0-9]{3}-[0-9]{2}-[0-9]{4}\b"),
}


def apply(*, policy: RedactionPolicy, text: Optional[str]) -> Optional[str]:
    """Apply redaction policy to text, masking PII based on configured rules."""
    if text is None:
        return None
    if policy.mode == RedactionMode.METADATA_ONLY:
        return None
    if policy.mode == RedactionMode.FULL:
        return text

    # REDACTED mode — apply masking rules
    result = text
    if policy.redact_emails:
        result = _PATTERNS["email"].sub("[EMAIL]", result)
    if policy.redact_phones:
        result = _PATTERNS["phone"].sub("[PHONE]", result)
    if policy.redact_ids:
        result = _PATTERNS["cpf"].sub("[CPF]", result)
        result = _PATTERNS["ssn"].sub("[SSN]", result)

    # Apply custom patterns
    for pattern_str in policy.custom_patterns:
        try:
            pattern = re.compile(pattern_str)
            result = pattern.sub("[REDACTED]", result)
        except re.error:
            pass

    return result
