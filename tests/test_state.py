from src.state import StateStore


def test_claim_then_sent_blocks_reprocessing(tmp_path):
    s = StateStore(tmp_path / "s.db")
    assert s.try_claim("<id1>", "a@b", "subj") is True
    s.transition("<id1>", "PARSED", matter_number="M12205", document_type="Exhibits")
    s.transition("<id1>", "SENT")
    assert s.is_sent("<id1>")
    assert s.try_claim("<id1>", "a@b", "subj") is False


def test_active_row_is_locked(tmp_path):
    s = StateStore(tmp_path / "s.db")
    assert s.try_claim("<id1>", "a@b", "subj")
    assert s.try_claim("<id1>", "a@b", "subj") is False


def test_failed_row_can_be_retried(tmp_path):
    s = StateStore(tmp_path / "s.db")
    assert s.try_claim("<id1>", "a@b", "subj")
    s.fail("<id1>", "boom")
    row = s.get("<id1>")
    assert (
        row["state"] == "FAILED"
        and row["last_error"] == "boom"
        and row["attempts"] == 1
    )
    assert s.try_claim("<id1>", "a@b", "subj") is True
    assert s.get("<id1>")["attempts"] == 2


def test_release_stale_active(tmp_path):
    s = StateStore(tmp_path / "s.db")
    s.try_claim("<id1>", "a@b", "subj")
    s.try_claim("<id2>", "a@b", "subj")
    s.transition("<id2>", "SENT")
    assert s.release_stale_active(max_age_seconds=0) == 1
    assert s.get("<id1>")["state"] == "FAILED"
    assert s.get("<id2>")["state"] == "SENT"


def test_fresh_active_request_is_not_released(tmp_path):
    s = StateStore(tmp_path / "s.db")
    s.try_claim("<id1>", "a@b", "subj")
    assert s.release_stale_active(max_age_seconds=3600) == 0
    assert s.get("<id1>")["state"] == "RECEIVED"


def test_claim_is_atomic_across_connections(tmp_path):
    path = tmp_path / "s.db"
    one, two = StateStore(path), StateStore(path)
    assert one.try_claim("<id1>", "a@b", "subj") is True
    assert two.try_claim("<id1>", "a@b", "subj") is False
