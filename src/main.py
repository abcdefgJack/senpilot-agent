"""Entry point: polling loop, or a one-shot local run for testing.

python -m src.main                       # poll the inbox forever
python -m src.main --once "Other Documents from M12205"   # no email, local ZIP
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import re
import shutil
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor

from .config import Config, load_config
from .email_client import EmailClient, InboundMessage, MessageTooLarge
from .pipeline import run_pipeline
from .rendering import render_failure, render_not_found, render_success, render_usage
from .request_parser import USAGE_TEXT, ParseError, parse_request
from .state import StateStore
from .uarb_client import MatterNotFound

log = logging.getLogger("agent")


def _safe_dirname(message_id: str) -> str:
    readable = (
        re.sub(r"[^A-Za-z0-9._-]", "_", message_id).strip("._-")[:48] or "request"
    )
    digest = hashlib.sha256(message_id.encode("utf-8", errors="replace")).hexdigest()[
        :16
    ]
    return f"{readable}-{digest}"


def _is_automated_sender(msg: InboundMessage, agent_email: str) -> bool:
    """Reject system/list mail before parsing so the agent cannot create reply loops."""
    sender = (msg.sender or "").strip().lower()
    local = sender.partition("@")[0]
    auto_submitted = (msg.auto_submitted or "").strip().lower()
    precedence = (msg.precedence or "").strip().lower()
    return (
        not sender
        or sender == agent_email.strip().lower()
        or auto_submitted not in ("", "no")
        or precedence in {"bulk", "junk", "list"}
        or any(
            token in local
            for token in (
                "no-reply",
                "noreply",
                "do-not-reply",
                "donotreply",
                "mailer-daemon",
            )
        )
    )


def _mail_client(cfg: Config) -> EmailClient:
    return EmailClient(
        cfg.agent_email,
        cfg.agent_password,
        cfg.imap_host,
        cfg.imap_port,
        cfg.smtp_host,
        cfg.smtp_port,
        cfg.max_message_bytes,
        cfg.network_timeout,
        cfg.max_inbound_bytes,
    )


def _send_reply(
    mail: EmailClient,
    msg: InboundMessage,
    body: str,
    attachment=None,
) -> Exception | None:
    try:
        mail.reply(msg, body, attachment=attachment)
    except Exception as exc:
        log.exception("could not send reply for %s", msg.message_id or msg.uid)
        return exc
    return None


def handle_message(
    msg: InboundMessage, cfg: Config, store: StateStore, mail: EmailClient
) -> bool:
    """Process one message and report whether its IMAP item may be marked read.

    Returning False keeps the message unread after an unexpected/incomplete run,
    allowing startup recovery to reclaim it instead of silently losing it.
    """
    if _is_automated_sender(msg, cfg.agent_email):
        log.info(
            "ignore automated/system message uid=%s sender=%s", msg.uid, msg.sender
        )
        return True

    mid = msg.message_id or f"uid-{msg.uid}"
    if store.get(mid) is None and store.is_rate_limited(
        msg.sender,
        cfg.rate_limit_per_sender_hour,
        cfg.global_rate_limit_per_hour,
    ):
        log.warning(
            "rate limit reached for sender=%s; ignoring uid=%s", msg.sender, msg.uid
        )
        return True

    if not store.try_claim(mid, msg.sender, msg.subject):
        log.info("skip %s: already sent or in progress", mid)
        return store.is_sent(mid)
    log.info("processing %s from %s subject=%r", mid, msg.sender, msg.subject)

    # 1. parse
    try:
        req = parse_request(f"{msg.subject}\n{msg.body}")
    except ParseError as exc:
        log.info("usage reply for %s: %s", mid, exc)
        if send_error := _send_reply(
            mail, msg, render_usage(msg.sender_name, USAGE_TEXT)
        ):
            store.fail(mid, f"usage reply failed: {send_error}")
            return False
        store.transition(mid, "SENT", last_error=f"usage: {exc}")
        return True
    store.transition(
        mid,
        "PARSED",
        matter_number=req.matter_number,
        document_type=req.document_type.value,
    )

    # 2. browser pipeline
    work_dir = cfg.work_dir / _safe_dirname(mid)
    try:
        for attempt in (1, 2):
            try:
                result = run_pipeline(
                    req, cfg, work_dir, on_state=lambda s: store.transition(mid, s)
                )
                break
            except MatterNotFound:
                raise
            except Exception:
                if attempt == 2:
                    raise
                log.exception("pipeline attempt 1 failed for %s; retrying once", mid)
                shutil.rmtree(work_dir, ignore_errors=True)
                store.transition(mid, "PARSED")
        body = render_success(
            msg.sender_name,
            result.info,
            req.document_type,
            len(result.files),
            result.zip_path.name if result.zip_path else None,
            result.skipped_oversize,
        )
        send_error = _send_reply(mail, msg, body, result.zip_path)
        if isinstance(send_error, MessageTooLarge):
            store.fail(mid, str(send_error))
            return (
                _send_reply(
                    mail,
                    msg,
                    render_failure(
                        msg.sender_name, req.matter_number, req.document_type
                    ),
                )
                is None
            )
        if send_error:
            store.fail(mid, f"success reply failed: {send_error}")
            return False
        store.transition(mid, "SENT")
        return True
    except MatterNotFound:
        if send_error := _send_reply(
            mail, msg, render_not_found(msg.sender_name, req.matter_number)
        ):
            store.fail(mid, f"not-found reply failed: {send_error}")
            return False
        store.transition(mid, "SENT", last_error="matter not found")
        return True
    except Exception as exc:
        log.exception("pipeline failed for %s", mid)
        store.fail(mid, str(exc))
        return (
            _send_reply(
                mail,
                msg,
                render_failure(msg.sender_name, req.matter_number, req.document_type),
            )
            is None
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _process_message(msg: InboundMessage, cfg: Config) -> None:
    store = StateStore(cfg.state_db_path)
    mail = _mail_client(cfg)
    try:
        if handle_message(msg, cfg, store, mail):
            mail.mark_read(msg.uid)
    finally:
        store.close()


def poll_forever(cfg: Config) -> None:
    store = StateStore(cfg.state_db_path)
    released = store.release_stale_active(cfg.stale_request_seconds)
    if released:
        log.warning("marked %d interrupted request(s) as FAILED for retry", released)
    store.close()
    mail = _mail_client(cfg)
    log.info(
        "polling %s every %ss with %d workers",
        cfg.agent_email,
        cfg.poll_interval,
        cfg.max_workers,
    )
    executor = ThreadPoolExecutor(
        max_workers=cfg.max_workers, thread_name_prefix="request"
    )
    pending: dict[Future[None], str] = {}
    try:
        while True:
            for future in list(pending):
                if future.done():
                    uid = pending.pop(future)
                    try:
                        future.result()
                    except Exception:
                        log.exception("worker failed for uid=%s", uid)
            try:
                capacity = cfg.max_workers - len(pending)
                if capacity > 0:
                    active_uids = set(pending.values())
                    for msg in mail.fetch_unread(limit=capacity):
                        if msg.uid in active_uids:
                            continue
                        pending[executor.submit(_process_message, msg, cfg)] = msg.uid
                        active_uids.add(msg.uid)
                        capacity -= 1
                        if capacity == 0:
                            break
            except Exception:
                log.exception("poll iteration failed")
            time.sleep(cfg.poll_interval)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def run_once(cfg: Config, text: str) -> int:
    try:
        req = parse_request(text)
    except ParseError as exc:
        print(f"Could not parse request ({exc}).\n\n{USAGE_TEXT}")
        return 2
    work_dir = cfg.work_dir / "once"
    try:
        result = run_pipeline(req, cfg, work_dir)
    except MatterNotFound:
        print(render_not_found("there", req.matter_number))
        return 1
    except Exception:
        log.exception("local run failed")
        print(
            "The request failed. Run again with --verbose and inspect the log for details."
        )
        return 1
    body = render_success(
        "there",
        result.info,
        req.document_type,
        len(result.files),
        result.zip_path.name if result.zip_path else None,
        result.skipped_oversize,
    )
    print(body)
    if result.zip_path:
        print(
            f"ZIP: {result.zip_path.resolve()} ({result.zip_path.stat().st_size} bytes)"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Senpilot regulatory filing agent")
    parser.add_argument(
        "--once", metavar="REQUEST_TEXT", help="run one request locally without email"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="backslashreplace")
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("playwright").setLevel(logging.WARNING)

    if args.once:
        return run_once(load_config(require_email=False), args.once)
    poll_forever(load_config())
    return 0


if __name__ == "__main__":
    sys.exit(main())
