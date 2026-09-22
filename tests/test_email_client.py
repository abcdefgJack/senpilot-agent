from pathlib import Path

import pytest

from src.email_client import EmailClient, InboundMessage, MessageTooLarge


def _message() -> InboundMessage:
    return InboundMessage(
        uid="1",
        message_id="<request@example.com>",
        sender="user@example.com",
        sender_name="User",
        subject="Request",
        body="Other Documents from M12205",
        references="",
    )


def test_encoded_message_limit_is_checked_before_smtp(tmp_path: Path):
    attachment = tmp_path / "x.zip"
    attachment.write_bytes(b"x" * 10_000)
    client = EmailClient(
        "agent@example.com", "x", "imap", 993, "smtp", 465, max_message_bytes=1000
    )
    with pytest.raises(MessageTooLarge, match="encoded email"):
        client.reply(_message(), "body", attachment)


def test_imap_search_omits_charset_for_gmail(monkeypatch):
    calls = []

    class FakeIMAP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def login(self, *args):
            pass

        def select(self, *args):
            pass

        def uid(self, command, *args):
            calls.append((command, args))
            return "OK", [b""]

    monkeypatch.setattr("src.email_client.imaplib.IMAP4_SSL", FakeIMAP)
    client = EmailClient("agent@example.com", "x", "imap", 993, "smtp", 465)
    assert client.fetch_unread() == []
    assert calls == [("search", (None, "UNSEEN"))]


def test_oversized_inbound_is_not_downloaded_and_is_marked_read(monkeypatch):
    calls = []

    class FakeIMAP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def login(self, *args):
            pass

        def select(self, *args):
            pass

        def uid(self, command, *args):
            calls.append((command, args))
            if command == "search":
                return "OK", [b"1 2"]
            if command == "fetch":
                return "OK", [b"1 (RFC822.SIZE 2000)"]
            return "OK", [b""]

    monkeypatch.setattr("src.email_client.imaplib.IMAP4_SSL", FakeIMAP)
    client = EmailClient(
        "agent@example.com",
        "x",
        "imap",
        993,
        "smtp",
        465,
        max_inbound_bytes=1000,
    )
    assert client.fetch_unread(limit=1) == []
    assert calls == [
        ("search", (None, "UNSEEN")),
        ("fetch", (b"1", "(RFC822.SIZE)")),
        ("store", (b"1", "+FLAGS", "(\\Seen)")),
    ]
