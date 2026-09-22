# Senpilot Regulatory Filing Agent — Implementation Plan

## 1. Goal

Build an email-driven agent that accepts a UARB matter number and one document type, collects up to 10 matching files, ZIPs them, and replies to the sender with the ZIP, current document counts, and matter metadata.

Supported document types:

- Exhibits
- Key Documents
- Other Documents
- Transcripts
- Recordings

Example request: `Can you give me Other Documents files from M12205?`

## 2. Definition of Done

The MVP is done when an email sent to the agent can complete this flow without manual intervention:

1. Detect a new request email exactly once.
2. Extract and validate `M#####` plus one supported document type.
3. Open the UARB public database and load the matter.
4. Read the matter title, type, category, status, filing dates, and all five document counts.
5. Open the requested tab and download `min(total, 10)` files.
6. Verify the downloaded files and create one ZIP.
7. Reply to the original sender with the ZIP and an accurate summary.
8. Record the request state so retries do not send duplicate replies.

## 3. Live Feasibility Check

Validated in Chrome on 2026-09-21 against `https://uarb.novascotia.ca/fmi/webd/UARB15`:

- Matter lookup for `M12205` works.
- Current matter metadata is readable:
  - Status: Open
  - Title: Halifax Regional Water Commission - Windsor Street Exchange Redevelopment Project - $69,275,000
  - Type: Capital Expenditure Approvals
  - Category: Water
  - Date received: 04/07/2025
  - Date final submissions: 10/23/2025
- Current counts are Exhibits 13, Key Documents 6, Other Documents 43, Transcripts 0, Recordings 0.
- A document row exposes `Preview` and `GO GET IT` controls.
- `GO GET IT` opens a download dialog with the real filename; clicking that filename produces a valid browser download.

The assignment's sample counts and dollar amount are stale. The reply must use values scraped at request time, not hard-coded sample text.

## 4. MVP Technical Design

Use Python 3.13 and Playwright. Keep parsing, browser automation, archive creation, and email transport as separate modules.

### Email ingress and reply

- Use a dedicated Gmail account.
- Poll unread mail over IMAP every 15 seconds.
- Use the Gmail message ID as the idempotency key.
- Reply over SMTP with the original sender and subject thread preserved.
- Store credentials only in environment variables; never commit them.
- For the fastest demo path, run the worker locally. After the end-to-end flow passes, package it in Docker and deploy only if a continuously available address is required.

### Request parsing

- Matter regex: `\bM\d{5}\b`, case-insensitive, normalized to uppercase.
- Match document types with whitespace/case normalization and singular/plural aliases.
- Deterministic parsing first. An LLM is unnecessary for the challenge example and adds latency and failure modes.
- If the matter number or document type is missing/ambiguous, send a short usage reply instead of guessing.

### UARB browser automation

- Launch one Playwright Chromium context per request with a unique temporary download directory.
- Navigate to the UARB15 database.
- The FileMaker matter-number field is not a normal HTML input. Click its stable visual location, type the matter number, and verify the visible value before searching.
- Use role/text locators for matter tabs, counts, metadata, `GO GET IT`, modal filenames, and modal close buttons.
- Read all five counts before opening the requested category.
- Download rows in displayed order until 10 files are saved or the list ends.
- Wait for each Playwright `download` event, save using the modal filename, and verify non-zero file size.
- Retry a failed page load or download once. Fail the request cleanly after the retry; never send a partial ZIP as if it were complete.

### ZIP creation

- Sanitize filenames and resolve duplicates with `-2`, `-3`, etc.
- Create `{matter}_{document_type}.zip` using Python `zipfile` with deflate compression.
- Include only successfully verified documents.
- Produce a small internal manifest for logs containing source row, filename, and byte size; do not add it to the customer ZIP unless needed for debugging.

### State and idempotency

Use SQLite with this state machine:

`RECEIVED -> PARSED -> MATTER_LOADED -> DOWNLOADED -> ZIPPED -> SENT`

Terminal failure state: `FAILED`, with the last error and attempt count. Before processing, reject any Gmail message ID already in `SENT` or currently active.

## 5. Suggested Repository Layout

```text
senpilot-agent/
  src/
    main.py             # polling loop and orchestration
    config.py           # environment validation
    models.py           # request, matter, document models
    request_parser.py   # matter/type extraction
    email_client.py     # IMAP receive and SMTP reply
    uarb_client.py      # Playwright navigation and downloads
    archive.py          # filename safety and ZIP creation
    state.py            # SQLite idempotency/state transitions
  tests/
    test_request_parser.py
    test_archive.py
    test_email_rendering.py
  .env.example
  requirements.txt
  Dockerfile
  README.md
  IMPLEMENTATION_PLAN.md
```

## 6. Three-Hour Build Order

### 0:00–0:20 — Scaffold and deterministic parser

- Create the package, configuration loader, models, and `.env.example`.
- Add parser tests for valid, invalid, lowercase, and ambiguous requests.

Exit condition: a raw email body becomes a validated request object or a specific validation error.

### 0:20–1:20 — UARB lookup and one real download

- Automate matter search.
- Extract metadata and five counts.
- Select the requested tab.
- Complete one verified `GO GET IT` download.

Exit condition: `M12205 + Other Documents` returns current metadata plus one local PDF.

### 1:20–1:50 — Ten-file loop and ZIP

- Download up to 10 rows.
- Handle duplicate names, zero-file categories, and per-file timeout.
- Create and inspect the ZIP.

Exit condition: ZIP member count equals `min(category_count, 10)` for the test matter.

### 1:50–2:25 — Email worker

- Poll one inbox, parse the message, run the pipeline, and reply with attachment.
- Preserve the reply thread and add idempotency storage.

Exit condition: one real request email receives exactly one reply with the ZIP attached.

### 2:25–2:45 — End-to-end hardening

- Test invalid matter, invalid document type, zero-document category, duplicate email, and one forced download failure.
- Add structured logs without credentials or full email bodies.

### 2:45–3:00 — Submission assets

- Finish README setup/run instructions.
- Push code to a public or evaluator-accessible repository.
- Record a sub-five-minute Loom showing the inbound email, browser collection, ZIP contents, and reply.
- Test the Loom and repository links in Incognito.

## 7. Reply Format

```text
Hi {sender_name},

{matter_number} is about {title}. It relates to {type} within the {category} category. Its status is {status}; the matter was received on {date_received} and has a final-submissions date of {date_final_submissions}.

I found {exhibits} Exhibits, {key_documents} Key Documents, {other_documents} Other Documents, {transcripts} Transcripts, and {recordings} Recordings. I downloaded {downloaded_count} out of {requested_type_count} {requested_type} files and attached them as {zip_name}.

Best,
Regulatory Filing Agent
```

## 8. Tests That Matter

- Parser accepts `m12205` and normalizes it to `M12205`.
- Parser rejects multiple matter numbers or multiple document types.
- Category count `0` creates no ZIP and returns an accurate no-files reply.
- Category count from 1 to 9 downloads every file.
- Category count above 10 downloads exactly 10.
- A failed download is retried once and never silently omitted.
- Reprocessing the same Gmail message ID does not send a second reply.
- ZIP contents are non-empty and match the download log.
- Generated email quotes the live UARB values, not sample values.

## 9. Risks and Explicit Fallbacks

- **FileMaker UI changes:** centralize visual coordinates and verify typed/search results before continuing. Prefer semantic locators everywhere else.
- **Gmail attachment limit:** calculate ZIP size before sending. If it exceeds the configured safe limit, fail clearly in the MVP rather than sending a broken email. A Drive link is a stretch feature because the assignment explicitly asks for an attachment.
- **Large recordings:** apply the same size guard before and during download.
- **Transient site failures:** one bounded retry with a fresh page; preserve logs and request state.
- **Duplicate processing:** SQLite message-ID lock plus `SENT` terminal state.
- **Credential leakage:** `.env` only, redacted logs, `.gitignore`, and a dedicated mailbox.

## 10. Scope Cuts

Do not spend the three-hour window on a dashboard, multi-user accounts, vector search, LLM-generated summaries, perfect deployment infrastructure, or polished UI. The strongest submission is one complete, observable email-to-ZIP-to-email path with honest failure handling.

