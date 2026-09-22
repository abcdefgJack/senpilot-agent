"""Deterministic extraction of a matter number and document type from an email."""

from __future__ import annotations

import re

from .models import DocumentType, FilingRequest

MATTER_RE = re.compile(r"\bM\d{5}\b", re.IGNORECASE)

# Alias -> canonical type. Matched after whitespace collapse, case-insensitively,
# with an optional plural "s".
_ALIASES: dict[str, DocumentType] = {
    "exhibit": DocumentType.EXHIBITS,
    "key document": DocumentType.KEY_DOCUMENTS,
    "key doc": DocumentType.KEY_DOCUMENTS,
    "other document": DocumentType.OTHER_DOCUMENTS,
    "other doc": DocumentType.OTHER_DOCUMENTS,
    "transcript": DocumentType.TRANSCRIPTS,
    "recording": DocumentType.RECORDINGS,
}

_TYPE_RE = re.compile(
    r"\b("
    + "|".join(re.escape(a) for a in sorted(_ALIASES, key=len, reverse=True))
    + r")s?\b",
    re.IGNORECASE,
)

_QUOTED_HISTORY_RE = re.compile(
    r"(?im)^(?:"
    r"\s*>"
    r"|\s*On\s.+?\swrote:\s*$"
    r"|\s*在.+写道[：:]\s*$"
    r"|\s*-{2,}\s*Original Message\s*-{2,}\s*$"
    r"|\s*_{5,}\s*$"
    r"|\s*From:\s.+$"
    r")"
)

USAGE_TEXT = (
    "I couldn't understand that request. Please include exactly one UARB matter "
    "number (for example M12205) and exactly one document type: Exhibits, Key "
    "Documents, Other Documents, Transcripts, or Recordings.\n\n"
    "Example: Can you give me Other Documents files from M12205?"
)


class ParseError(ValueError):
    """Raised when the email cannot be turned into a single unambiguous request."""


def latest_message_text(text: str) -> str:
    """Return only the new, top-posted part of an email conversation."""
    value = text or ""
    marker = _QUOTED_HISTORY_RE.search(value)
    if marker:
        value = value[: marker.start()]
    return value.strip()


def parse_request(text: str) -> FilingRequest:
    normalized = re.sub(r"\s+", " ", latest_message_text(text))

    matters = {m.group(0).upper() for m in MATTER_RE.finditer(normalized)}
    if not matters:
        raise ParseError("no matter number found")
    if len(matters) > 1:
        raise ParseError(f"multiple matter numbers found: {sorted(matters)}")

    types = {_ALIASES[m.group(1).lower()] for m in _TYPE_RE.finditer(normalized)}
    if not types:
        raise ParseError("no document type found")
    if len(types) > 1:
        raise ParseError(
            f"multiple document types found: {sorted(t.value for t in types)}"
        )

    return FilingRequest(matter_number=matters.pop(), document_type=types.pop())
