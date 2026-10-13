# Phase 13 — Email Watcher

**Status:** Written under the owner's standing approval (2026-09-30); assumptions are listed at the end for review · **Date:** 2026-09-30 · **Branch:** `feat/v1.2-beyond-the-request`
**Covers:** MAIL-01..03 (`docs/requirements.md`), PRD §11.9
**Roadmap:** Phase 13 · **Builds on:** Phase 9 (traces outside requests), the watcher framework

## Goal

Every email the app sends during a trace is recorded with:
- its recipients and subject
- its text and HTML bodies
- its headers, the backend, the names and sizes of its attachments, and the outcome

The dashboard can show the message as it would arrive: a safe HTML preview, plus a `.eml` download.

## Decisions

| Decision | Choice |
|---|---|
| Hook | Replace `django.core.mail.get_connection` with a wrapper that returns the real backend, with its `send_messages` wrapped on that instance. `send_mail`, `send_mass_mail`, `mail_admins`, `mail_managers` and `EmailMessage.send()` all look the function up at call time, so one patch covers them. |
| Event | One `mail.sent` event per message, recorded after `send_messages` returns or raises |
| Default | `"mail": False`: opt in. Email bodies routinely hold password-reset and sign-in links whose tokens no masking rule can recognise. |
| Bodies | Text and HTML bodies are kept up to `MAX_MAIL_BODY_CHARS = 65536` characters each and masked with `mask_value` |
| Attachments | Filename, MIME type and size only. Contents are never stored. |
| Preview | A separate endpoint serves the stored HTML with its own locked-down CSP (sandboxed, no scripts, no remote loads), shown in a sandboxed same-origin `<iframe>` |
| `.eml` | Rebuilt at download time from the stored fields as a MIME message. Attachments are listed in an `X-Inspector-Omitted-Attachments` header instead of included. |

Alternatives rejected:
- **Patching each backend's `send_messages`:** every backend class overrides it, and third-party backends would be missed.
- **Patching `EmailMessage.message()`:** some API-based backends (e.g. Anymail) never call it, and it runs again for every recipient batch.
- **Storing the full MIME source:** attachments make it unbounded, and it duplicates the fields.
- **Rendering the HTML inline in the dashboard page:** email HTML is untrusted content. It must never run inside the dashboard's own origin context.

## Watcher (`django_inspector/watchers/mail.py`)

`MailWatcher(BaseWatcher)`, `watcher_name = "mail"`, added to `apps.WATCHER_MODULES`, with `WATCHERS["mail"] = False` in `conf.DEFAULTS`.

**Hooks:**
- `install_hooks()` saves `django.core.mail.get_connection` and sets `django.core.mail.get_connection = _get_connection`. It never double-patches (a marker guards this).
- `remove_hooks()` restores the original.
- Backend instances created while hooked keep their wrapper. The wrapper checks `watcher.is_capturing()` on every call, so it goes inert once the watcher is disabled.

**`_get_connection(*args, **kwargs)`:**
- It calls the original.
- It sets an instance attribute `send_messages` that wraps the backend's bound method, marked so a backend is never wrapped twice.
- It returns the backend.
- Wrapping failures are logged, and the unwrapped backend is returned.

**`send_messages` wrapper:**
1. If `not watcher.is_capturing()`, call the original.
2. Otherwise:
   - Record the start time.
   - Call the original with the same list.
   - Record one event per message with `status` set to `"sent"` if the backend's return count covers it and `"failed"` otherwise. When the original raised, every message gets `"error"` plus the error's type and message.
   - Re-raise the exception, if any, unchanged.
3. Building metadata is guarded: a failure logs and never affects sending.

**`mail.sent` metadata:**

| Key | Value |
|---|---|
| `subject` | `str`, truncated to 998 characters (the RFC line limit) |
| `from_email`, `to`, `cc`, `bcc`, `reply_to` | Strings or lists of strings, not masked (recipients are the point of the tab) |
| `text_body` | The message `body`, masked and truncated; `text_body_truncated` flag |
| `html_body` | The first `text/html` alternative (`EmailMultiAlternatives.alternatives`, which may be tuples or `EmailAlternative` objects depending on the Django version; read both), masked and truncated; `html_body_truncated` flag |
| `attachments` | `[{filename, mimetype, size}]` for each attachment. Tuples, `MIMEBase` objects and Django 5.2 `EmailAttachment` are handled; the size is the byte length of the content when available, else `None` |
| `headers` | `extra_headers`, masked by key |
| `backend` | The backend class's dotted path |
| `message_class` | The message class's dotted path |
| `status` | `"sent"`, `"failed"` or `"error"`; `error` is `"Type: message"` when set |
| `duration_ms` | The whole `send_messages` call, shared by the batch |

Phase 8 fills `duration_ms` on the event through its generic step.

## Dashboard

**Mail list** (`mail/`, `mail-list`, sidebar entry "Mail"):
- Columns: to (the first 2 recipients and a count), subject, status badge, backend short name, time.
- Filters: `to` (icontains on `metadata__to` cast to text; the worker checks this is portable, and otherwise filters in Python on the bounded page), `subject` and `status`.
- It has CSV export like the other lists (Phase 12 pattern).
- It uses the existing HTMX list pattern.

**Mail detail** (`mail/<pk>/`, `mail-detail`):
- A header block with from/to/cc/bcc/reply-to/subject/status/backend.
- Tabs for "HTML" (the preview iframe), "Text" (a `<pre>`) and "Headers".
- The attachment list, a "Download .eml" button, and a link to the parent trace (the request page, or the trace page for traces that aren't requests).

**Preview** (`mail/<pk>/preview/`, `mail-preview`):
- It returns the stored `html_body` as `text/html`, wrapped in `inspector_required`.
- It is decorated `xframe_options_sameorigin`.
- It sets its own `Content-Security-Policy`: `default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; sandbox; frame-ancestors 'self'`. The dashboard's CSP helper only adds its policy when none is set, so this one stands.
- It returns 404 when the message has no HTML body.
- The detail template embeds it as `<iframe sandbox src="..." referrerpolicy="no-referrer">`, with no `allow-scripts` and no `allow-same-origin`.

**`.eml`** (`mail/<pk>/message.eml`, `mail-eml`):
- It builds an `EmailMultiAlternatives` from the stored fields (no connection, never sent), adding the HTML alternative when present and the omitted-attachments header.
- It returns `message().as_bytes()` as `message/rfc822` with `Content-Disposition: attachment; filename="<subject-slug or mail-<pk>>.eml"`.
- It adds no extra masking step. The fields were already masked when stored, so the file never contains more than the dashboard shows.

**Request detail and trace pages:** `mail.sent` events already show up in their timelines through the shared timeline and waterfall. Add a "Mail" count and a short list (subject → mail detail) on the request detail page, following the Logs/Signals pattern from Phase 6.

## Error handling

- Sending always behaves exactly as without the watcher: same return value, same exceptions.
- Watcher failures are logged (or raised under `INSPECTOR_RAISE_ERRORS`, but never while a send exception is propagating).
- The preview and `.eml` views return 404 for events that aren't `mail.sent`.

## Testing

| File | Covers |
|---|---|
| `tests/test_mail_watcher.py` (new) | Lifecycle: install replaces `get_connection`, remove restores it, no double-patching, a disabled watcher is inert even for backends created while it was on. `send_mail`, `send_mass_mail` (2 messages → 2 events), `EmailMessage.send`, `EmailMultiAlternatives` with HTML and `mail_admins` inside a trace, using the locmem backend: event fields, masking of bodies and headers, truncation flags, attachments from tuple and `MIMEBase` forms (sizes, never contents). Sending still works and returns the same count. Backend raising → `status: "error"` and the exception propagates. A `fail_silently` backend returning 0 → `"failed"`. No active trace → no event. Correlation with the trace id inside `trace()` and a request. A metadata failure doesn't break sending. |
| `tests/test_dashboard_pages.py` / `test_dashboard_features.py` | Mail list, filters, detail page, sidebar entry, access control on list, detail, preview and eml. Preview: CSP header exact, `X-Frame-Options: SAMEORIGIN`, 404 without HTML. The `.eml` parses back with `email.message_from_bytes` into the same subject, recipients and bodies, with the omitted-attachments header. The detail iframe has `sandbox` without `allow-scripts`. Request detail shows the mail count. |
| `tests/test_csv_export.py` | Mail list CSV |
| `tests/test_checks.py`, `tests/test_docs.py` | The `WATCHERS["mail"]` row and default |

Then the full gates.

## Docs

- README: a "Mail" section (opt-in, why it's off by default, what's stored, preview safety, `.eml` without attachments) and the `WATCHERS["mail"]` row.
- CHANGELOG: the 0.3.0 section, citing MAIL-01..03.
- Roadmap and requirements: tick Phase 13 and MAIL-01..03.

## Assumptions (for the owner's end-of-milestone review)

1. **The mail watcher is off by default,** because bodies can hold reset or sign-in tokens.
2. **Attachment contents are never stored.** The `.eml` download lists omitted attachments in a header instead.
3. **Recipients aren't masked.**
4. **Bodies are capped at 64K characters each.**
5. **Messages sent through a backend created without `get_connection`** (instantiating a backend class directly) aren't captured.
