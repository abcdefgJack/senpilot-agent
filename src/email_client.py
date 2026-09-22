"""IMAP polling for unread requests and SMTP replies that stay in-thread."""

from __future__ import annotations

import email
import hashlib
import imaplib
import logging
import re
import smtplib
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import parseaddr
from pathlib import Path

log = logging.getLogger(__name__)
_RFC822_SIZE = re.compile(rb"RFC822\.SIZE\s+(\d+)")


class MessageTooLarge(RuntimeError):
    pass


@dataclass
class InboundMessage:
    uid: str  # IMAP UID (session-scoped handle)
    message_id: str  # RFC 5322 Message-ID — the idempotency key
    sender: str  # bare address
    sender_name: str
    subject: str
    body: str
    references: str
    auto_submitted: str = ""
    precedence: str = ""


def _body_text(msg: email.message.EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    text = part.get_content()
    if part.get_content_type() == "text/html":
        text = re.sub(r"<[^>]+>", " ", text)
    return text


def _inbound_message(uid: bytes, parts) -> InboundMessage | None:
    if (
        not parts
        or not isinstance(parts[0], tuple)
        or not isinstance(parts[0][1], bytes)
    ):
        return None
    raw = parts[0][1]
    msg = email.message_from_bytes(raw, policy=policy.default)
    name, addr = parseaddr(msg.get("From", ""))
    name = re.sub(r"[\r\n]+", " ", name).strip()[:80]
    addr = addr.strip().lower() if "@" in addr else ""
    return InboundMessage(
        uid=uid.decode(),
        message_id=(msg.get("Message-ID") or "").strip()
        or f"<sha256-{hashlib.sha256(raw).hexdigest()}@local>",
        sender=addr,
        sender_name=name or addr.split("@")[0],
        subject=msg.get("Subject", "") or "",
        body=_body_text(msg),
        references=(msg.get("References") or "").strip(),
        auto_submitted=(msg.get("Auto-Submitted") or "").strip(),
        precedence=(msg.get("Precedence") or "").strip(),
    )


class EmailClient:
    def __init__(
        self,
        address: str,
        password: str,
        imap_host: str,
        imap_port: int,
        smtp_host: str,
        smtp_port: int,
        max_message_bytes: int = 24_000_000,
        network_timeout: int = 30,
        max_inbound_bytes: int = 1_000_000,
    ):
        self.address = address
        self.password = password
        self.imap_host, self.imap_port = imap_host, imap_port
        self.smtp_host, self.smtp_port = smtp_host, smtp_port
        self.max_message_bytes = max_message_bytes
        self.network_timeout = network_timeout
        self.max_inbound_bytes = max_inbound_bytes

    # -- receive -------------------------------------------------------------

    def _ignore_oversized(self, imap, uid: bytes) -> bool:
        status, parts = imap.uid("fetch", uid, "(RFC822.SIZE)")
        blob = b" ".join(part for part in parts or [] if isinstance(part, bytes))
        match = _RFC822_SIZE.search(blob)
        if status != "OK" or match is None:
            log.warning("could not preflight message size for uid=%r", uid)
            return True
        if int(match.group(1)) <= self.max_inbound_bytes:
            return False
        status, _ = imap.uid("store", uid, "+FLAGS", "(\\Seen)")
        if status != "OK":
            raise RuntimeError(f"IMAP mark-read failed: {status}")
        log.warning("ignored oversized inbound message uid=%r", uid)
        return True

    def fetch_unread(self, limit: int | None = None) -> list[InboundMessage]:
        """Return unread inbox messages. Does NOT mark them read; call mark_read()."""
        out: list[InboundMessage] = []
        with imaplib.IMAP4_SSL(
            self.imap_host, self.imap_port, timeout=self.network_timeout
        ) as imap:
            imap.login(self.address, self.password)
            imap.select("INBOX")
            # imaplib requires None here to omit the optional charset. Its stub
            # incorrectly types the argument as str.
            status, data = imap.uid("search", None, "UNSEEN")  # type: ignore[arg-type]
            if status != "OK":
                raise RuntimeError(f"IMAP search failed: {status}")
            uids = data[0].split() if data and data[0] else []
            if limit is not None:
                uids = uids[: max(0, limit)]
            for uid in uids:
                if self._ignore_oversized(imap, uid):
                    continue
                status, parts = imap.uid("fetch", uid, "(BODY.PEEK[])")
                inbound = _inbound_message(uid, parts) if status == "OK" else None
                if inbound:
                    out.append(inbound)
        return out

    def mark_read(self, uid: str) -> None:
        with imaplib.IMAP4_SSL(
            self.imap_host, self.imap_port, timeout=self.network_timeout
        ) as imap:
            imap.login(self.address, self.password)
            imap.select("INBOX")
            status, _ = imap.uid("store", uid, "+FLAGS", "(\\Seen)")
            if status != "OK":
                raise RuntimeError(f"IMAP mark-read failed: {status}")

    # -- send ----------------------------------------------------------------

    def reply(
        self, original: InboundMessage, body: str, attachment: Path | None = None
    ) -> None:
        msg = EmailMessage()
        msg["From"] = self.address
        msg["To"] = original.sender
        msg["Auto-Submitted"] = "auto-replied"
        msg["X-Auto-Response-Suppress"] = "All"
        subject = original.subject or "UARB document request"
        msg["Subject"] = (
            subject if subject.lower().startswith("re:") else f"Re: {subject}"
        )
        if original.message_id:
            msg["In-Reply-To"] = original.message_id
            refs = f"{original.references} {original.message_id}".strip()
            msg["References"] = refs
        msg.set_content(body)
        if attachment is not None:
            msg.add_attachment(
                attachment.read_bytes(),
                maintype="application",
                subtype="zip",
                filename=attachment.name,
            )
        encoded_size = len(msg.as_bytes(policy=SMTP))
        if encoded_size > self.max_message_bytes:
            raise MessageTooLarge(
                f"encoded email is {encoded_size} bytes, above the {self.max_message_bytes}-byte limit"
            )
        with smtplib.SMTP_SSL(
            self.smtp_host, self.smtp_port, timeout=self.network_timeout
        ) as smtp:
            smtp.login(self.address, self.password)
            refused = smtp.send_message(msg)
            if refused:
                raise RuntimeError(f"SMTP refused {len(refused)} recipient(s)")
        log.info(
            "replied to %s (attachment=%s)",
            original.sender,
            attachment.name if attachment else None,
        )
