from src.models import DocumentType, MatterInfo
from src.rendering import render_failure, render_not_found, render_success, render_usage
from src.request_parser import USAGE_TEXT


def _info(**overrides):
    base = {
        "matter_number": "M12205",
        "title": "Halifax Regional Water Commission - Windsor Street Exchange Redevelopment Project - $69,275,000",
        "matter_type": "Water",
        "category": "Capital Expenditure Approvals",
        "status": "Open",
        "date_received": "04/07/2025",
        "date_final_submissions": "10/23/2025",
        "counts": {
            DocumentType.EXHIBITS: 13,
            DocumentType.KEY_DOCUMENTS: 6,
            DocumentType.OTHER_DOCUMENTS: 43,
            DocumentType.TRANSCRIPTS: 0,
            DocumentType.RECORDINGS: 0,
        },
    }
    base.update(overrides)
    return MatterInfo(**base)


def test_success_uses_live_values_not_sample_text():
    body = render_success(
        "Jane", _info(), DocumentType.OTHER_DOCUMENTS, 10, "M12205_Other_Documents.zip"
    )
    assert body.startswith("Hi Jane,")
    assert "M12205 is about Halifax Regional Water Commission" in body
    assert "$69,275,000" in body
    assert "relates to Water within the Capital Expenditure Approvals category" in body
    assert "status is Open" in body
    assert "received on 04/07/2025" in body
    assert "final-submissions date of 10/23/2025" in body
    assert (
        "13 Exhibits, 6 Key Documents, 43 Other Documents, 0 Transcripts, and 0 Recordings"
        in body
    )
    assert "downloaded 10 out of 43 Other Documents files" in body
    assert "attached them as M12205_Other_Documents.zip" in body
    assert body.rstrip().endswith("Regulatory Filing Agent")


def test_success_counts_change_with_input():
    info = _info(counts={**_info().counts, DocumentType.OTHER_DOCUMENTS: 7})
    body = render_success("Jane", info, DocumentType.OTHER_DOCUMENTS, 7, "x.zip")
    assert "7 Other Documents" in body
    assert "downloaded 7 out of 7" in body


def test_zero_files_has_no_attachment_line():
    body = render_success("Jane", _info(), DocumentType.TRANSCRIPTS, 0, None)
    assert "no Transcripts files on this matter" in body
    assert "attached" not in body
    assert "0 Transcripts" in body


def test_success_explains_files_skipped_for_email_size():
    body = render_success(
        "Jane",
        _info(),
        DocumentType.EXHIBITS,
        6,
        "M12205_Exhibits.zip",
        skipped_oversize=4,
    )
    assert "skipped 4 file(s)" in body
    assert "email attachment limits" in body


def test_missing_metadata_falls_back():
    body = render_success(
        "Jane", _info(title="", status=""), DocumentType.EXHIBITS, 1, "z.zip"
    )
    assert "is about not listed" in body
    assert "status is not listed" in body


def test_usage_reply():
    body = render_usage("Bob", USAGE_TEXT)
    assert "Hi Bob," in body and "M12205" in body and "Exhibits" in body


def test_not_found_and_failure():
    assert "couldn't find matter M99999" in render_not_found("Bob", "M99999")
    f = render_failure("Bob", "M12205", DocumentType.EXHIBITS)
    assert "Exhibits files from M12205" in f and "No partial ZIP" in f
    assert "C:\\secret" not in f
