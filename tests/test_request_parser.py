import pytest

from src.models import DocumentType
from src.request_parser import ParseError, parse_request


def test_example_request():
    r = parse_request("Can you give me Other Documents files from M12205?")
    assert r.matter_number == "M12205"
    assert r.document_type == DocumentType.OTHER_DOCUMENTS


def test_lowercase_matter_is_normalized():
    r = parse_request("please send exhibits for m12205")
    assert r.matter_number == "M12205"
    assert r.document_type == DocumentType.EXHIBITS


@pytest.mark.parametrize(
    "text,expected",
    [
        ("key documents M00001", DocumentType.KEY_DOCUMENTS),
        ("Key   Document\nM00001", DocumentType.KEY_DOCUMENTS),
        ("key docs M00001", DocumentType.KEY_DOCUMENTS),
        ("the transcript for M00001", DocumentType.TRANSCRIPTS),
        ("RECORDINGS from M00001", DocumentType.RECORDINGS),
        ("other doc M00001", DocumentType.OTHER_DOCUMENTS),
        ("exhibit M00001", DocumentType.EXHIBITS),
    ],
)
def test_aliases(text, expected):
    assert parse_request(text).document_type == expected


def test_same_matter_repeated_is_fine():
    r = parse_request("M12205 exhibits please, again that's m12205")
    assert r.matter_number == "M12205"


def test_same_type_repeated_is_fine():
    r = parse_request("Exhibits, exhibit, EXHIBITS for M12205")
    assert r.document_type == DocumentType.EXHIBITS


def test_missing_matter():
    with pytest.raises(ParseError, match="no matter"):
        parse_request("send me the exhibits")


def test_missing_type():
    with pytest.raises(ParseError, match="no document type"):
        parse_request("send me everything from M12205")


def test_multiple_matters_rejected():
    with pytest.raises(ParseError, match="multiple matter"):
        parse_request("exhibits from M12205 and M12206")


def test_multiple_types_rejected():
    with pytest.raises(ParseError, match="multiple document types"):
        parse_request("exhibits and transcripts from M12205")


def test_matter_needs_exactly_five_digits():
    with pytest.raises(ParseError):
        parse_request("exhibits from M1220")
    with pytest.raises(ParseError):
        parse_request("exhibits from M122055")


def test_empty_body():
    with pytest.raises(ParseError):
        parse_request("")


def test_quoted_gmail_history_is_not_parsed():
    text = """Key Documents from M12205

On Sun, Sep 20, 2026 at 1:00 PM Someone <x@example.com> wrote:
> Transcripts from M12205
"""
    assert parse_request(text).document_type == DocumentType.KEY_DOCUMENTS


def test_outlook_original_message_is_not_parsed():
    text = """Exhibits from M12205
-----Original Message-----
From: Someone <x@example.com>
Other Documents from M99999
"""
    result = parse_request(text)
    assert result.matter_number == "M12205"
    assert result.document_type == DocumentType.EXHIBITS
