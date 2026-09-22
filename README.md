# Senpilot Regulatory Filing Agent

An email-driven agent for the Nova Scotia UARB public documents database.
Send it an email such as

> Can you give me Other Documents files from M12205?

and it looks the matter up at `https://uarb.novascotia.ca/fmi/webd/UARB15`,
downloads up to 10 files from the requested category, ZIPs them, and replies in
the same thread with the ZIP plus the matter's live metadata and document counts.

Supported document types: **Exhibits, Key Documents, Other Documents, Transcripts, Recordings**.

## How it works

```
Gmail (IMAP, unread)  ->  parse M##### + type  ->  Playwright / Chromium on UARB
       ^                                                    |
       |                                        metadata, 5 counts, GO GET IT x N
       +------ SMTP reply (thread preserved) <---- ZIP  <---+
                        SQLite state machine deduplicates by Message-ID
```

| Module | Responsibility |
|---|---|
| `src/request_parser.py` | Deterministic `M#####` + document-type extraction; rejects ambiguous mail |
| `src/uarb_client.py` | Playwright automation of the FileMaker WebDirect UI |
| `src/archive.py` | Filename sanitising, `-2/-3` de-duplication, deflated ZIP |
| `src/email_client.py` | IMAP polling + SMTP reply with `In-Reply-To`/`References` |
| `src/rendering.py` | Reply text (pure functions) |
| `src/state.py` | SQLite `RECEIVED -> PARSED -> MATTER_LOADED -> DOWNLOADED -> ZIPPED -> SENT` / `FAILED` |
| `src/pipeline.py` | Request -> matter -> downloads -> ZIP (no email) |
| `src/main.py` | Polling loop and `--once` local runner |

## Setup

Requires Python 3.10+ (developed on 3.13; Docker uses 3.10).

```bash
pip install -r requirements-dev.txt
python -m playwright install chromium
cp .env.example .env      # then fill in the Gmail credentials
```

Use a **dedicated Gmail account** with 2-step verification and an
[App Password](https://myaccount.google.com/apppasswords). IMAP must be enabled
in that account's Gmail settings. Put the address and app password in `.env`
(`AGENT_EMAIL`, `AGENT_EMAIL_PASSWORD`). `.env` is git-ignored.

## Run

Local dry run without any email (prints the reply and writes the ZIP to `work/once/`):

```bash
python -m src.main --once "Can you give me Other Documents files from M12205?"
```

Email worker (polls every `POLL_INTERVAL_SECONDS`, default 15, with two bounded workers):

```bash
python -m src.main
```

Set `HEADLESS=0` in `.env` to watch the browser.

Docker:

```bash
docker build -t senpilot-agent .
docker run --env-file .env -v senpilot-data:/data senpilot-agent
```

## Tests

```bash
python -m pytest
```

Unit tests cover the parser, ZIP creation, state machine, reply rendering, and
the worker's idempotency / failure paths with a fake mailbox and fake pipeline.
The browser path is exercised with `--once` against the live site.

Verified live on 2026-09-21 with `M12205`:

| Request | Result |
|---|---|
| Other Documents (43) | 10 files, 10.4 MB ZIP |
| Key Documents (6) | 6 files |
| Transcripts (0) | accurate "nothing to attach" reply, no ZIP |
| Exhibits (13) | 6-file ZIP; oversized individual PDFs are skipped and reported by count |
| `M99999` | "matter not found" reply |

## Behaviour and guarantees

- **Idempotent normal processing.** The RFC `Message-ID` is the idempotency key in
  SQLite. Claiming is atomic, so a message in `SENT` or an active state is not
  processed concurrently, including across worker processes.
  The browser pipeline gets one fresh-browser retry before a failure reply. An
  inbox item is marked read only after handling completes, so an interrupted run
  can be reclaimed after recovery. As with any SMTP worker, a process crash in the
  narrow window after SMTP accepts a reply but before SQLite records `SENT` can
  still produce a duplicate after recovery.
- **No corrupt ZIPs.** Each download is retried once. If a file still fails, the
  request fails with no attachment. If the next valid file would exceed the safe
  attachment budget, the agent sends the valid leading files already collected.
- **Live values only.** Title, type, category, status, dates, and all five
  counts are scraped at request time.
- **Attachment size guard.** `MAX_ATTACHMENT_BYTES` (default 17 MB) limits binary
  downloads, and `MAX_MESSAGE_BYTES` (default 24 MB) checks the fully encoded MIME
  message before SMTP. `MAX_INBOUND_BYTES` (default 1 MB) rejects oversized input
  before downloading its MIME body. This accounts for base64 overhead and bounds
  mailbox memory use.
- **Unparseable mail** (no matter number, several matter numbers, or several
  document types) gets a short usage reply.
- **Automated mail is ignored.** Messages from no-reply/daemon senders, the
  agent itself, or messages marked auto-submitted/bulk/list are marked read
  without a response, preventing loops and replies to provider security mail.
- **Quoted history is ignored.** Only the newest top-posted message section is
  parsed, so an earlier request in Gmail/Outlook history cannot make it ambiguous.
- **Bounded concurrency and rate limiting.** Slow downloads do not block all later
  mail. Each sender is capped by `RATE_LIMIT_PER_SENDER_HOUR`, and all senders
  together are capped by `GLOBAL_RATE_LIMIT_PER_HOUR`.
- **Temporary files are removed.** Per-email downloads and ZIPs are
  deleted after delivery or a terminal failure.
- **Credentials** live only in environment variables; logs contain no
  credentials or email bodies.

## Notes on the UARB site

- The FileMaker matter field is a `div` that becomes `contenteditable` about a
  second after focus; typing earlier drops keystrokes. The client waits, types,
  and verifies the visible value before searching (`FIELD_FOCUS_SETTLE_MS`).
- Layout object ids differ between the matter page and each tab, so metadata is
  read by label-column geometry, not ids.
- On the site, **Type** is the sector (e.g. `Water`) and **Category** is the
  matter kind (e.g. `Capital Expenditure Approvals`). The implementation plan
  listed these the other way round; the reply follows the site.
- Some rows genuinely share one PDF (e.g. Other Documents `102329` and `102330`
  are byte-identical on the server). They are kept as separate members because
  that is what the database lists.
- Confidential exhibit rows still expose a download dialog and are treated like
  any other row.
- The document grid is virtualised (~12 rows rendered); the client scrolls when
  a requested row is not yet rendered.

## Not in scope (deliberately)

Dashboard, multi-user accounts, LLM parsing, Drive links for oversize archives,
and deployment infrastructure beyond the Dockerfile.
