"""Reply text rendering. Pure functions so they can be unit-tested."""

from __future__ import annotations

from .models import DocumentType, MatterInfo

SIGNATURE = "Best,\nRegulatory Filing Agent"


def _n(value: str, fallback: str = "not listed") -> str:
    return value.strip() if value and value.strip() else fallback


def render_success(
    sender_name: str,
    info: MatterInfo,
    doc_type: DocumentType,
    downloaded_count: int,
    zip_name: str | None,
    skipped_oversize: int = 0,
) -> str:
    c = info.counts
    requested_total = info.count_for(doc_type)
    if downloaded_count > 0 and zip_name:
        files_line = (
            f"I downloaded {downloaded_count} out of {requested_total} {doc_type.value} files "
            f"and attached them as {zip_name}."
        )
        if skipped_oversize:
            files_line += (
                f" I skipped {skipped_oversize} file(s) that could not fit safely within "
                "email attachment limits."
            )
    else:
        files_line = f"There are currently no {doc_type.value} files on this matter, so there is nothing to attach."
    return (
        f"Hi {sender_name},\n\n"
        f"{info.matter_number} is about {_n(info.title)}. It relates to {_n(info.matter_type)} within the "
        f"{_n(info.category)} category. Its status is {_n(info.status)}; the matter was received on "
        f"{_n(info.date_received)} and has a final-submissions date of {_n(info.date_final_submissions)}.\n\n"
        f"I found {c.get(DocumentType.EXHIBITS, 0)} Exhibits, {c.get(DocumentType.KEY_DOCUMENTS, 0)} Key Documents, "
        f"{c.get(DocumentType.OTHER_DOCUMENTS, 0)} Other Documents, {c.get(DocumentType.TRANSCRIPTS, 0)} Transcripts, "
        f"and {c.get(DocumentType.RECORDINGS, 0)} Recordings. {files_line}\n\n"
        f"{SIGNATURE}\n"
    )


def render_usage(sender_name: str, usage_text: str) -> str:
    return f"Hi {sender_name},\n\n{usage_text}\n\n{SIGNATURE}\n"


def render_not_found(sender_name: str, matter_number: str) -> str:
    return (
        f"Hi {sender_name},\n\n"
        f"I couldn't find matter {matter_number} in the UARB public documents database. "
        f"Please double-check the matter number and try again.\n\n{SIGNATURE}\n"
    )


def render_failure(sender_name: str, matter_number: str, doc_type: DocumentType) -> str:
    return (
        f"Hi {sender_name},\n\n"
        f"I wasn't able to complete your request for {doc_type.value} files from {matter_number}. "
        "The public database or email service did not complete the request successfully.\n\n"
        f"No partial ZIP was sent. Please try again later.\n\n{SIGNATURE}\n"
    )
