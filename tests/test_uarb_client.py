from pathlib import Path

import pytest

from src.models import DownloadedFile
from src.uarb_client import DownloadError, UARBClient


def _download(tmp_path: Path, index: int, size: int) -> DownloadedFile:
    path = tmp_path / f"{index}.pdf"
    path.write_bytes(b"x" * size)
    return DownloadedFile(index, path.name, path, size)


def test_download_rows_stops_before_attachment_budget(tmp_path, monkeypatch):
    client = UARBClient("https://example.invalid")
    sizes = [4, 4, 4]
    monkeypatch.setattr(
        client, "_download_row_once", lambda i, dest: _download(tmp_path, i, sizes[i])
    )
    files = client.download_rows(3, tmp_path / "downloads", max_total_bytes=10)
    assert [f.size_bytes for f in files] == [4, 4]
    assert not (tmp_path / "2.pdf").exists()


def test_download_rows_fails_if_no_file_can_fit(tmp_path, monkeypatch):
    client = UARBClient("https://example.invalid")
    monkeypatch.setattr(
        client, "_download_row_once", lambda i, dest: _download(tmp_path, i, 11)
    )
    with pytest.raises(DownloadError, match="none of the requested files"):
        client.download_rows(1, tmp_path / "downloads", max_total_bytes=10)


def test_download_rows_skips_large_file_and_keeps_looking(tmp_path, monkeypatch):
    client = UARBClient("https://example.invalid")
    sizes = [20, 4, 4]
    monkeypatch.setattr(
        client, "_download_row_once", lambda i, dest: _download(tmp_path, i, sizes[i])
    )
    files = client.download_rows(3, tmp_path / "downloads", max_total_bytes=10)
    assert [f.row_index for f in files] == [1, 2]
    assert not (tmp_path / "0.pdf").exists()
