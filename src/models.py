"""Plain data models shared across modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class DocumentType(str, Enum):
    EXHIBITS = "Exhibits"
    KEY_DOCUMENTS = "Key Documents"
    OTHER_DOCUMENTS = "Other Documents"
    TRANSCRIPTS = "Transcripts"
    RECORDINGS = "Recordings"

    @property
    def slug(self) -> str:
        return self.value.replace(" ", "_")


@dataclass(frozen=True)
class FilingRequest:
    matter_number: str  # normalized, e.g. "M12205"
    document_type: DocumentType


@dataclass
class MatterInfo:
    matter_number: str
    title: str
    matter_type: str
    category: str
    status: str
    date_received: str
    date_final_submissions: str
    counts: dict[DocumentType, int] = field(default_factory=dict)

    def count_for(self, doc_type: DocumentType) -> int:
        return self.counts.get(doc_type, 0)


@dataclass
class DownloadedFile:
    row_index: int
    original_name: str
    path: Path
    size_bytes: int
