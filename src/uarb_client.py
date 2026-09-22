"""Playwright automation for the UARB FileMaker WebDirect public database.

The page is a FileMaker WebDirect (Vaadin) app: fields are divs that turn into
contenteditable editors on focus, the document list is a virtualised grid, and
downloads go through a modal. Everything here was validated against the live
site; see IMPLEMENTATION_PLAN.md section 3.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    Playwright,
    sync_playwright,
)
from playwright.sync_api import (
    TimeoutError as PlaywrightTimeout,
)
from typing_extensions import Self

from .archive import sanitize_filename
from .models import DocumentType, DownloadedFile, MatterInfo

log = logging.getLogger(__name__)

MATTER_PLACEHOLDER = "eg M01234"
TAB_RE = re.compile(
    r"^(Exhibits|Key Documents|Other Documents|Transcripts|Recordings) - (\d+)$"
)
FOUND_COUNT_RE = re.compile(r"Found Count:\s*(\d+)")

PAGE_LOAD_TIMEOUT_MS = 60_000
ACTION_TIMEOUT_MS = 30_000
# Time for the FileMaker field to swap in its editor after focus. Typing earlier
# than this silently drops the first keystrokes.
FIELD_FOCUS_SETTLE_MS = 1_000


class UARBError(RuntimeError):
    pass


class MatterNotFound(UARBError):
    pass


class DownloadError(UARBError):
    pass


@dataclass
class _Box:
    text: str
    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2


class UARBClient:
    """One browser context per request, with its own temp download directory."""

    def __init__(self, url: str, headless: bool = True, download_timeout_s: int = 120):
        self.url = url
        self.headless = headless
        self.download_timeout_ms = download_timeout_s * 1000
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._ctx: BrowserContext | None = None
        self._page: Page | None = None

    # -- lifecycle -----------------------------------------------------------

    def __enter__(self) -> Self:
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        self._ctx = self._browser.new_context(
            viewport={"width": 1400, "height": 1000}, accept_downloads=True
        )
        self._ctx.set_default_timeout(ACTION_TIMEOUT_MS)
        self._page = self._ctx.new_page()
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        if self._ctx:
            self._ctx.close()
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("UARBClient must be entered before use")
        return self._page

    # -- navigation ----------------------------------------------------------

    def open_database(self) -> None:
        page = self.page
        page.goto(self.url, wait_until="networkidle", timeout=PAGE_LOAD_TIMEOUT_MS)
        self._matter_field().wait_for(state="visible")
        page.wait_for_timeout(1500)  # let the Vaadin layout finish settling

    def _matter_field(self) -> Locator:
        return self.page.locator(
            f"div.inner_border:has(div.placeholder:text-is('{MATTER_PLACEHOLDER}')) > div.text"
        )

    def _type_matter_number(self, matter_number: str) -> None:
        field = self._matter_field()
        for attempt in range(3):
            field.click()
            self.page.wait_for_timeout(FIELD_FOCUS_SETTLE_MS)
            self.page.keyboard.press("Control+A")
            self.page.keyboard.press("Backspace")
            self.page.keyboard.type(matter_number, delay=40)
            self.page.wait_for_timeout(400)
            typed = field.inner_text().strip()
            if typed == matter_number:
                return
            log.warning(
                "matter field shows %r after typing %r (attempt %d)",
                typed,
                matter_number,
                attempt + 1,
            )
        raise UARBError(f"could not enter matter number; field shows {typed!r}")

    def _go_directly_search_button(self) -> Locator:
        """The Search button beside the matter field (there are three 'Search' buttons)."""
        fb = self._matter_field().bounding_box()
        if fb is None:
            raise UARBError("matter field is not visible")
        buttons = self.page.get_by_role("button", name="Search", exact=True)
        for i in range(buttons.count()):
            bb = buttons.nth(i).bounding_box()
            if bb and abs(bb["y"] - fb["y"]) < 40 and bb["x"] > fb["x"]:
                return buttons.nth(i)
        raise UARBError("could not find the 'Go Directly to Matter' Search button")

    def load_matter(self, matter_number: str) -> MatterInfo:
        page = self.page
        self._type_matter_number(matter_number)
        self._go_directly_search_button().click()

        tabs = page.get_by_text(re.compile(r"^Exhibits - \d+$"))
        not_found = page.get_by_text("No Records Found", exact=True)
        try:
            tabs.or_(not_found).first.wait_for(timeout=ACTION_TIMEOUT_MS)
        except PlaywrightTimeout as exc:
            raise UARBError("matter page did not load") from exc
        if not_found.count():
            raise MatterNotFound(f"UARB returned no records for {matter_number}")
        page.wait_for_timeout(1500)

        counts = self._read_counts()
        meta = self._read_metadata()
        if meta.get("matter_number", "").upper() != matter_number.upper():
            raise UARBError(
                f"loaded matter {meta.get('matter_number')!r} does not match requested {matter_number!r}"
            )
        info = MatterInfo(
            matter_number=matter_number,
            title=meta.get("title", ""),
            matter_type=meta.get("type", ""),
            category=meta.get("category", ""),
            status=meta.get("status", ""),
            date_received=meta.get("date_received", ""),
            date_final_submissions=meta.get("date_final_submissions", ""),
            counts=counts,
        )
        log.info("loaded %s: %s", matter_number, info)
        return info

    # -- scraping ------------------------------------------------------------

    def _read_counts(self) -> dict[DocumentType, int]:
        texts: list[str] = self.page.evaluate(
            "() => Array.from(document.querySelectorAll('.fm-widget')).map(e => (e.innerText || '').trim())"
        )
        counts: dict[DocumentType, int] = {}
        for t in texts:
            m = TAB_RE.match(t)
            if m:
                counts[DocumentType(m.group(1))] = int(m.group(2))
        missing = [d for d in DocumentType if d not in counts]
        if missing:
            raise UARBError(f"could not read counts for: {[d.value for d in missing]}")
        return counts

    def _boxes(self, selector: str) -> list[_Box]:
        raw = self.page.evaluate(
            """(sel) => Array.from(document.querySelectorAll(sel)).map(e => {
                 const r = e.getBoundingClientRect();
                 return {text: (e.innerText || '').replace(/\\s+/g, ' ').trim(),
                         x: r.x, y: r.y, w: r.width, h: r.height};
               }).filter(b => b.w > 0 && b.h > 0)""",
            selector,
        )
        return [_Box(**b) for b in raw]

    def _read_metadata(self) -> dict[str, str]:
        """Map metadata labels to the field values rendered beneath them.

        Layout object ids change between FileMaker layouts, so this uses geometry:
        a value belongs to the label whose column it sits under. Stacked labels
        ("Matter No / Status", "Type / Category") share one column and are
        ordered top-to-bottom.
        """
        labels = self._boxes(".v-label, .fm-text-paragraph")
        fields = self._boxes(".fm-textarea .inner_border > .text")
        statics = self._boxes(".v-label")

        def find_label(*names: str) -> _Box | None:
            for name in names:
                for b in labels:
                    if b.text == name:
                        return b
            return None

        def under(
            label: _Box | None, pool: list[_Box], max_dy: float = 120
        ) -> list[_Box]:
            if label is None:
                return []
            hits = [
                b
                for b in pool
                if b.y > label.y
                and (b.y - (label.y + label.h)) < max_dy
                and b.x < label.cx < b.x + b.w
            ]
            return sorted(hits, key=lambda b: b.y)

        out: dict[str, str] = {}
        for names, keys in (
            (("Matter No Status", "Matter No"), ("matter_number", "status")),
            (("Type Category", "Type"), ("type", "category")),
            (("Date Received",), ("date_received",)),
            (
                ("Date Final Submissions", "Decision Date"),
                ("date_final_submissions",),
            ),
        ):
            for key, box in zip(keys, under(find_label(*names), fields)):
                out[key] = box.text

        title_label = find_label("Title - Description")
        col = under(
            title_label,
            [s for s in statics if s.text and s.text != "Title - Description"],
            max_dy=200,
        )
        if col:
            out["title"] = col[0].text

        # Fallback for the matter number: any field that looks like one.
        if "matter_number" not in out:
            out["matter_number"] = next(
                (b.text for b in fields if re.fullmatch(r"M\d{5}", b.text)), ""
            )
        log.debug("metadata: %s", out)
        return out

    # -- documents -----------------------------------------------------------

    def open_document_tab(self, doc_type: DocumentType, expected_count: int) -> int:
        """Click the tab and return the number of rows it reports.

        Layouts differ per tab: some show "Found Count: N", others (Key Documents)
        only the grid. Wait for either, then prefer the explicit count.
        """
        page = self.page
        tab_pattern = re.compile(rf"^{re.escape(doc_type.value)} - \d+$")
        found = page.get_by_text(FOUND_COUNT_RE)
        last_error: PlaywrightTimeout | None = None
        for attempt in range(1, 4):
            tab = page.get_by_text(tab_pattern).first
            try:
                tab.wait_for(state="visible", timeout=ACTION_TIMEOUT_MS)
                tab.click(force=attempt > 1)
                if expected_count == 0:
                    page.wait_for_timeout(1500)
                    return 0
                found.or_(self._rows()).first.wait_for(timeout=ACTION_TIMEOUT_MS)
                break
            except PlaywrightTimeout as exc:
                last_error = exc
                log.warning(
                    "%s tab attempt %d did not render rows", doc_type.value, attempt
                )
                self._close_dialog_if_open()
                page.keyboard.press("Escape")
                page.wait_for_timeout(1000 * attempt)
        else:
            raise UARBError(
                f"{doc_type.value} tab did not load after 3 attempts"
            ) from last_error
        page.wait_for_timeout(1500)
        n = expected_count
        if found.count():
            m = FOUND_COUNT_RE.search(found.first.inner_text())
            if m:
                n = int(m.group(1))
        if n != expected_count:
            log.warning(
                "tab count %d != header count %d; using tab count", n, expected_count
            )
        if n > 0 and self._rows().count() == 0:
            raise UARBError(
                f"{doc_type.value} tab reports {n} files but rendered no rows"
            )
        return n

    def _rows(self) -> Locator:
        return self.page.locator("tr.v-grid-row")

    def _row(self, index: int) -> Locator:
        rows = self._rows()
        if index >= rows.count():
            # Virtualised grid: scroll the last rendered row into view to render more.
            rows.last.scroll_into_view_if_needed()
            self.page.wait_for_timeout(800)
        if index >= rows.count():
            raise UARBError(f"row {index} is not rendered (only {rows.count()} rows)")
        row = rows.nth(index)
        row.scroll_into_view_if_needed()
        return row

    def _close_dialog_if_open(self) -> None:
        dlg = self.page.locator(".v-window.fm-modal-dialog")
        if dlg.count():
            try:
                dlg.get_by_role("button", name="Close").click(timeout=5000)
                dlg.wait_for(state="detached", timeout=5000)
            except PlaywrightTimeout:
                self.page.keyboard.press("Escape")

    def _download_row_once(self, index: int, dest_dir: Path) -> DownloadedFile:
        page = self.page
        row = self._row(index)
        # Column order differs per tab; the longest cell is the title (for logs only).
        cells = [t.strip() for t in row.locator(".fm-textarea .text").all_inner_texts()]
        row_title = max(cells, key=len, default="")
        row.get_by_role("button", name="GO GET IT").click()

        dlg = page.locator(".v-window.fm-modal-dialog")
        dlg.wait_for(timeout=ACTION_TIMEOUT_MS)
        file_btn = dlg.locator(".fm-download-button")
        file_btn.first.wait_for(timeout=ACTION_TIMEOUT_MS)
        modal_name = file_btn.first.inner_text().strip()
        if not modal_name:
            raise DownloadError(f"row {index}: download dialog has no filename")

        with page.expect_download(timeout=self.download_timeout_ms) as dl_info:
            file_btn.first.click()
        download = dl_info.value
        name = download.suggested_filename or modal_name
        dest = dest_dir / f"{index:02d}_{sanitize_filename(name)}"
        download.save_as(dest)
        size = dest.stat().st_size if dest.exists() else 0
        if size == 0:
            raise DownloadError(f"row {index}: {name} saved with zero bytes")

        self._close_dialog_if_open()
        log.info(
            "row %d: %s (%d bytes) %s",
            index,
            name,
            size,
            f"[{row_title}]" if row_title else "",
        )
        return DownloadedFile(
            row_index=index, original_name=modal_name, path=dest, size_bytes=size
        )

    def download_rows(
        self, count: int, dest_dir: Path, max_total_bytes: int | None = None
    ) -> list[DownloadedFile]:
        """Download rows 0..count-1 in displayed order. Each row is retried once."""
        dest_dir.mkdir(parents=True, exist_ok=True)
        files: list[DownloadedFile] = []
        total = 0
        for i in range(count):
            last_err: Exception | None = None
            for attempt in (1, 2):
                try:
                    f = self._download_row_once(i, dest_dir)
                    break
                except (PlaywrightTimeout, UARBError) as exc:
                    last_err = exc
                    log.warning("row %d attempt %d failed: %s", i, attempt, exc)
                    self._close_dialog_if_open()
                    self.page.wait_for_timeout(1000)
            else:
                raise DownloadError(
                    f"row {i} failed after retry: {last_err}"
                ) from last_err
            if max_total_bytes is not None and total + f.size_bytes > max_total_bytes:
                f.path.unlink(missing_ok=True)
                log.info(
                    "skipping row %d (%d bytes): %d bytes are already selected and the budget is %d",
                    i,
                    f.size_bytes,
                    total,
                    max_total_bytes,
                )
                continue
            files.append(f)
            total += f.size_bytes
        if count > 0 and not files:
            raise DownloadError(
                "none of the requested files fit within the attachment limit"
            )
        return files
