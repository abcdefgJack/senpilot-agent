"""Request -> matter metadata -> downloads -> ZIP. No email here."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .archive import create_zip, zip_name
from .config import Config
from .models import DownloadedFile, FilingRequest, MatterInfo
from .uarb_client import UARBClient

log = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    info: MatterInfo
    files: list[DownloadedFile]
    zip_path: Path | None  # None when the category has zero files
    skipped_oversize: int = 0


def run_pipeline(
    req: FilingRequest, cfg: Config, work_dir: Path, on_state=lambda state: None
) -> PipelineResult:
    """Execute the browser part of a request.

    `on_state` is called with MATTER_LOADED / DOWNLOADED / ZIPPED so the caller
    can persist progress. Raises UARBError subclasses on failure; never returns
    a partial ZIP.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    with UARBClient(
        cfg.uarb_url, headless=cfg.headless, download_timeout_s=cfg.download_timeout
    ) as client:
        client.open_database()
        info = client.load_matter(req.matter_number)
        on_state("MATTER_LOADED")

        available = info.count_for(req.document_type)
        if available == 0:
            log.info(
                "%s has zero %s files; nothing to download",
                req.matter_number,
                req.document_type.value,
            )
            on_state("DOWNLOADED")
            on_state("ZIPPED")
            return PipelineResult(info=info, files=[], zip_path=None)

        tab_count = client.open_document_tab(req.document_type, available)
        # The header count and tab count should agree; trust the tab when they differ
        # (it is what the rows are drawn from) but keep the header in the summary.
        wanted = min(tab_count, cfg.max_files)
        files = client.download_rows(
            wanted, work_dir / "downloads", max_total_bytes=cfg.max_attachment_bytes
        )
        if not files:
            raise RuntimeError(
                "none of the requested files fit within the attachment limit"
            )
        on_state("DOWNLOADED")

        zpath = work_dir / zip_name(req.matter_number, req.document_type.slug)
        create_zip(files, zpath)
        size = zpath.stat().st_size
        if size > cfg.max_attachment_bytes:
            raise RuntimeError(
                f"ZIP is {size} bytes, above the {cfg.max_attachment_bytes}-byte attachment limit"
            )
        on_state("ZIPPED")
        log.info("zipped %d files into %s (%d bytes)", len(files), zpath.name, size)
        return PipelineResult(
            info=info,
            files=files,
            zip_path=zpath,
            skipped_oversize=wanted - len(files),
        )
