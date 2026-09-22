"""Environment-backed configuration. Fails fast on missing required values."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    agent_email: str
    agent_password: str
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int
    poll_interval: int
    max_files: int
    max_attachment_bytes: int
    max_message_bytes: int
    max_inbound_bytes: int
    download_timeout: int
    network_timeout: int
    max_workers: int
    rate_limit_per_sender_hour: int
    global_rate_limit_per_hour: int
    stale_request_seconds: int
    state_db_path: Path
    work_dir: Path
    headless: bool
    uarb_url: str


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def _int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}, got {value}")
    return value


def load_config(require_email: bool = True) -> Config:
    load_dotenv()
    if require_email:
        email = _require("AGENT_EMAIL")
        password = _require("AGENT_EMAIL_PASSWORD")
    else:
        email = os.environ.get("AGENT_EMAIL", "")
        password = os.environ.get("AGENT_EMAIL_PASSWORD", "")
    return Config(
        agent_email=email,
        agent_password=password,
        imap_host=os.environ.get("IMAP_HOST", "imap.gmail.com"),
        imap_port=_int("IMAP_PORT", 993),
        smtp_host=os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        smtp_port=_int("SMTP_PORT", 465),
        poll_interval=_int("POLL_INTERVAL_SECONDS", 15),
        max_files=_int("MAX_FILES", 10),
        # Keep the binary ZIP comfortably below Gmail's encoded-message limit.
        max_attachment_bytes=_int("MAX_ATTACHMENT_BYTES", 17_000_000),
        max_message_bytes=_int("MAX_MESSAGE_BYTES", 24_000_000),
        max_inbound_bytes=_int("MAX_INBOUND_BYTES", 1_000_000),
        download_timeout=_int("DOWNLOAD_TIMEOUT_SECONDS", 120),
        network_timeout=_int("NETWORK_TIMEOUT_SECONDS", 30),
        max_workers=_int("MAX_WORKERS", 2),
        rate_limit_per_sender_hour=_int("RATE_LIMIT_PER_SENDER_HOUR", 10),
        global_rate_limit_per_hour=_int("GLOBAL_RATE_LIMIT_PER_HOUR", 50),
        stale_request_seconds=_int("STALE_REQUEST_SECONDS", 1800),
        state_db_path=Path(os.environ.get("STATE_DB_PATH", "state.db")),
        work_dir=Path(os.environ.get("WORK_DIR", "work")),
        headless=os.environ.get("HEADLESS", "1").strip() not in ("0", "false", "False"),
        uarb_url=os.environ.get(
            "UARB_URL", "https://uarb.novascotia.ca/fmi/webd/UARB15"
        ),
    )
