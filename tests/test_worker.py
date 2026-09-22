"""Worker-level tests with a fake mailbox and a fake pipeline (no browser, no network)."""

from pathlib import Path

import pytest

from src import main as worker
from src.config import Config
from src.email_client import InboundMessage
from src.models import DocumentType, DownloadedFile, MatterInfo
from src.pipeline import PipelineResult
from src.state import StateStore
from src.uarb_client import DownloadError, MatterNotFound


class FakeMail:
    def __init__(self):
        self.sent: list[tuple[str, str, Path | None]] = []

    def reply(self, original, body, attachment=None):
        self.sent.append((original.sender, body, attachment))


def _cfg(tmp_path) -> Config:
    return Config(
        agent_email="agent@example.com",
        agent_password="x",
        imap_host="",
        imap_port=993,
        smtp_host="",
        smtp_port=465,
        poll_interval=1,
        max_files=10,
        max_attachment_bytes=17_000_000,
        max_message_bytes=24_000_000,
        max_inbound_bytes=1_000_000,
        download_timeout=10,
        network_timeout=30,
        max_workers=2,
        rate_limit_per_sender_hour=10,
        global_rate_limit_per_hour=50,
        stale_request_seconds=1800,
        state_db_path=tmp_path / "state.db",
        work_dir=tmp_path / "work",
        headless=True,
        uarb_url="https://example.invalid",
    )


def _msg(body="Can you give me Other Documents files from M12205?", mid="<abc@mail>"):
    return InboundMessage(
        uid="1",
        message_id=mid,
        sender="jane@example.com",
        sender_name="Jane",
        subject="Request",
        body=body,
        references="",
    )


def _info():
    return MatterInfo(
        "M12205",
        "Title",
        "Water",
        "Capital Expenditure Approvals",
        "Open",
        "04/07/2025",
        "10/23/2025",
        {d: 0 for d in DocumentType} | {DocumentType.OTHER_DOCUMENTS: 43},
    )


def _fake_pipeline_ok(tmp_path):
    def run(req, cfg, work_dir, on_state=lambda s: None):
        for s in ("MATTER_LOADED", "DOWNLOADED", "ZIPPED"):
            on_state(s)
        work_dir.mkdir(parents=True, exist_ok=True)
        z = work_dir / "M12205_Other_Documents.zip"
        z.write_bytes(b"PK\x05\x06" + b"\0" * 18)
        files = [DownloadedFile(i, f"{i}.pdf", z, 10) for i in range(10)]
        return PipelineResult(info=_info(), files=files, zip_path=z)

    return run


def test_happy_path_sends_one_reply_with_zip(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "run_pipeline", _fake_pipeline_ok(tmp_path))
    cfg, store, mail = _cfg(tmp_path), StateStore(tmp_path / "s.db"), FakeMail()
    worker.handle_message(_msg(), cfg, store, mail)
    assert len(mail.sent) == 1
    to, body, att = mail.sent[0]
    assert to == "jane@example.com" and att.name == "M12205_Other_Documents.zip"
    assert "downloaded 10 out of 43 Other Documents" in body
    assert store.get("<abc@mail>")["state"] == "SENT"
    assert not any(cfg.work_dir.iterdir())


def test_duplicate_message_id_does_not_send_twice(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "run_pipeline", _fake_pipeline_ok(tmp_path))
    cfg, store, mail = _cfg(tmp_path), StateStore(tmp_path / "s.db"), FakeMail()
    worker.handle_message(_msg(), cfg, store, mail)
    worker.handle_message(_msg(), cfg, store, mail)
    assert len(mail.sent) == 1


def test_unparseable_request_gets_usage_reply_and_no_browser(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("pipeline must not run")

    monkeypatch.setattr(worker, "run_pipeline", boom)
    cfg, store, mail = _cfg(tmp_path), StateStore(tmp_path / "s.db"), FakeMail()
    worker.handle_message(_msg(body="send me everything please"), cfg, store, mail)
    assert len(mail.sent) == 1 and "couldn't understand" in mail.sent[0][1]
    assert mail.sent[0][2] is None
    assert store.get("<abc@mail>")["state"] == "SENT"


def test_matter_not_found_reply(tmp_path, monkeypatch):
    def nf(*a, **k):
        raise MatterNotFound("no records")

    monkeypatch.setattr(worker, "run_pipeline", nf)
    cfg, store, mail = _cfg(tmp_path), StateStore(tmp_path / "s.db"), FakeMail()
    worker.handle_message(_msg(body="exhibits from M99999"), cfg, store, mail)
    assert len(mail.sent) == 1 and "couldn't find matter M99999" in mail.sent[0][1]
    assert store.get("<abc@mail>")["state"] == "SENT"


def test_download_failure_is_reported_and_retryable(tmp_path, monkeypatch):
    def fail(*a, **k):
        raise DownloadError("row 3 failed after retry")

    monkeypatch.setattr(worker, "run_pipeline", fail)
    cfg, store, mail = _cfg(tmp_path), StateStore(tmp_path / "s.db"), FakeMail()
    worker.handle_message(_msg(), cfg, store, mail)
    assert len(mail.sent) == 1
    body, att = mail.sent[0][1], mail.sent[0][2]
    assert (
        "row 3 failed after retry" not in body
        and "No partial ZIP" in body
        and att is None
    )
    row = store.get("<abc@mail>")
    assert row["state"] == "FAILED" and row["attempts"] == 1
    # A FAILED request can be claimed again (e.g. after the site recovers)
    monkeypatch.setattr(worker, "run_pipeline", _fake_pipeline_ok(tmp_path))
    worker.handle_message(_msg(), cfg, store, mail)
    assert len(mail.sent) == 2 and mail.sent[1][2] is not None
    assert store.get("<abc@mail>")["state"] == "SENT"


def test_transient_pipeline_failure_retries_once_before_reply(tmp_path, monkeypatch):
    calls = 0
    success = _fake_pipeline_ok(tmp_path)

    def flaky(req, cfg, work_dir, on_state=lambda s: None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise DownloadError("temporary site failure")
        return success(req, cfg, work_dir, on_state)

    monkeypatch.setattr(worker, "run_pipeline", flaky)
    cfg, store, mail = _cfg(tmp_path), StateStore(tmp_path / "s.db"), FakeMail()
    assert worker.handle_message(_msg(), cfg, store, mail) is True
    assert calls == 2
    assert len(mail.sent) == 1 and mail.sent[0][2] is not None
    assert store.get("<abc@mail>")["state"] == "SENT"


def test_zero_file_category_sends_no_attachment(tmp_path, monkeypatch):
    def run(req, cfg, work_dir, on_state=lambda s: None):
        return PipelineResult(info=_info(), files=[], zip_path=None)

    monkeypatch.setattr(worker, "run_pipeline", run)
    cfg, store, mail = _cfg(tmp_path), StateStore(tmp_path / "s.db"), FakeMail()
    worker.handle_message(_msg(body="transcripts from M12205"), cfg, store, mail)
    assert mail.sent[0][2] is None and "no Transcripts files" in mail.sent[0][1]


@pytest.mark.parametrize(
    "msg",
    [
        _msg(mid="<n1>"),
        InboundMessage(
            uid="2",
            message_id="<n2>",
            sender="no-reply@google.com",
            sender_name="Google",
            subject="Security alert",
            body="",
            references="",
        ),
        InboundMessage(
            uid="3",
            message_id="<n3>",
            sender="list@example.com",
            sender_name="List",
            subject="Digest",
            body="",
            references="",
            precedence="bulk",
        ),
        InboundMessage(
            uid="4",
            message_id="<n4>",
            sender="robot@example.com",
            sender_name="Robot",
            subject="Status",
            body="",
            references="",
            auto_submitted="auto-generated",
        ),
    ],
)
def test_automated_and_own_mail_is_ignored(tmp_path, monkeypatch, msg):
    if msg.message_id == "<n1>":
        msg.sender = "agent@example.com"

    def boom(*a, **k):
        raise AssertionError("pipeline must not run for automated mail")

    monkeypatch.setattr(worker, "run_pipeline", boom)
    cfg, store, mail = _cfg(tmp_path), StateStore(tmp_path / "s.db"), FakeMail()
    assert worker.handle_message(msg, cfg, store, mail) is True
    assert mail.sent == []
    assert store.get(msg.message_id) is None


def test_safe_dirname_uses_hash_to_avoid_long_id_collision():
    prefix = "x" * 100
    assert worker._safe_dirname(prefix + "a") != worker._safe_dirname(prefix + "b")


def test_rate_limit_skips_browser_and_reply(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("pipeline must not run for rate-limited mail")

    monkeypatch.setattr(worker, "run_pipeline", boom)
    cfg, store, mail = _cfg(tmp_path), StateStore(tmp_path / "s.db"), FakeMail()
    for i in range(cfg.rate_limit_per_sender_hour):
        assert store.try_claim(f"<old-{i}>", "jane@example.com", "Request")
        store.transition(f"<old-{i}>", "SENT")
    assert worker.handle_message(_msg(mid="<new>"), cfg, store, mail) is True
    assert mail.sent == []
    assert store.get("<new>") is None


def test_global_rate_limit_cannot_be_bypassed_with_new_senders(tmp_path, monkeypatch):
    monkeypatch.setattr(
        worker, "run_pipeline", lambda *a, **k: pytest.fail("pipeline must not run")
    )
    cfg, store, mail = _cfg(tmp_path), StateStore(tmp_path / "s.db"), FakeMail()
    for i in range(cfg.global_rate_limit_per_hour):
        assert store.try_claim(f"<old-{i}>", f"sender-{i}@example.com", "Request")
        store.transition(f"<old-{i}>", "SENT")
    assert worker.handle_message(_msg(mid="<global-new>"), cfg, store, mail) is True
    assert mail.sent == []
