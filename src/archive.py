"""Filename sanitising and ZIP creation."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from .models import DownloadedFile

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = re.compile(
    r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", re.IGNORECASE
)


def sanitize_filename(name: str, fallback: str = "document", max_length: int = 120) -> str:
    name = (name or "").strip().replace(" ", " ")
    name = _UNSAFE.sub("_", name)
    name = name.strip(" .")
    name = Path(name).name  # drop any path components
    if not name:
        return fallback
    if _RESERVED.match(name):
        name = f"_{name}"
    suffix = Path(name).suffix[:20]
    stem_limit = max(1, max_length - len(suffix))
    return f"{Path(name).stem[:stem_limit]}{suffix}"[:max_length]


def unique_name(name: str, taken: set[str]) -> str:
    """Return `name`, or `stem-2.ext`, `stem-3.ext`, ... if already taken."""
    if name.lower() not in taken:
        taken.add(name.lower())
        return name
    p = Path(name)
    n = 2
    while True:
        candidate = f"{p.stem}-{n}{p.suffix}"
        if candidate.lower() not in taken:
            taken.add(candidate.lower())
            return candidate
        n += 1


def zip_name(matter_number: str, doc_type_slug: str) -> str:
    return f"{matter_number}_{doc_type_slug}.zip"


def create_zip(files: list[DownloadedFile], out_path: Path) -> Path:
    """Write verified files into a deflated ZIP. Raises if any file is empty/missing."""
    taken: set[str] = set()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            if not f.path.exists() or f.path.stat().st_size == 0:
                raise ValueError(f"refusing to zip empty/missing file: {f.path}")
            arcname = unique_name(sanitize_filename(f.original_name), taken)
            zf.write(f.path, arcname)
    return out_path
