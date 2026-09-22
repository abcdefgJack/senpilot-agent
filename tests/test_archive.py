import zipfile

import pytest

from src.archive import create_zip, sanitize_filename, unique_name, zip_name
from src.models import DownloadedFile


def test_sanitize_strips_unsafe_chars():
    assert sanitize_filename('a<b>:"c/d\\e|f?g*h.pdf') == "a_b___c_d_e_f_g_h.pdf"


def test_sanitize_drops_path_components():
    out = sanitize_filename("../../etc/passwd")
    assert "/" not in out and "\\" not in out and not out.startswith(".")


def test_sanitize_fallback_for_empty():
    assert sanitize_filename("   ") == "document"
    assert sanitize_filename("...") == "document"


def test_sanitize_bounds_length_and_windows_device_names():
    assert len(sanitize_filename(f"{'a' * 200}.pdf")) == 120
    assert sanitize_filename("CON.pdf") == "_CON.pdf"


def test_unique_name_suffixes():
    taken: set[str] = set()
    assert unique_name("doc.pdf", taken) == "doc.pdf"
    assert unique_name("doc.pdf", taken) == "doc-2.pdf"
    assert unique_name("DOC.PDF", taken) == "DOC-3.PDF"
    assert unique_name("other.pdf", taken) == "other.pdf"


def test_zip_name():
    assert zip_name("M12205", "Other_Documents") == "M12205_Other_Documents.zip"


def _file(tmp_path, idx, name, content=b"data"):
    p = tmp_path / f"src_{idx}"
    p.write_bytes(content)
    return DownloadedFile(
        row_index=idx, original_name=name, path=p, size_bytes=len(content)
    )


def test_create_zip_members_and_dedup(tmp_path):
    files = [
        _file(tmp_path, 1, "a.pdf", b"one"),
        _file(tmp_path, 2, "a.pdf", b"two"),
        _file(tmp_path, 3, "b:c.pdf", b"three"),
    ]
    out = create_zip(files, tmp_path / "out" / "x.zip")
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert names == ["a.pdf", "a-2.pdf", "b_c.pdf"]
        assert zf.read("a-2.pdf") == b"two"
        assert zf.getinfo("a.pdf").compress_type == zipfile.ZIP_DEFLATED


def test_create_zip_rejects_empty_file(tmp_path):
    files = [_file(tmp_path, 1, "empty.pdf", b"")]
    with pytest.raises(ValueError, match="empty"):
        create_zip(files, tmp_path / "x.zip")
