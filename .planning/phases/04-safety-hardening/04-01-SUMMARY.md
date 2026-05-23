# Summary: Plan 04-01 — Sensitive-Data Masking + Sampling

**Phase:** 4 — Safety & Hardening
**Plan:** 1 of 3
**Status:** Complete
**Completed:** 2026-05-23

## What Was Built

### New Modules
- **`django_inspector/masking.py`** — Key-based + value-pattern redaction. Built-in `_BUILTIN_SENSITIVE_KEYS` (20 keys), extra keys from `SENSITIVE_KEYS` setting. Recursive dict/list/tuple walk (depth cap 10). CC-shaped and JWT-shaped value scanning.
- **`django_inspector/sampling.py`** — `ContextVar`-based sampling decision. `compute_sampling_decision()` checks rate + 5xx status + slow-request threshold. `set_sampled()` / `is_sampled()` for context propagation.

### Modified Files
- **`django_inspector/conf.py`** — Added `SENSITIVE_KEYS`, `SAMPLING_RATE`, `SLOW_REQUEST_THRESHOLD_MS` to `DEFAULTS`.
- **`django_inspector/watchers/base.py`** — `BaseWatcher.record()` now calls `mask_metadata()` before `buffer_event()`. Masking failures log at `WARNING` and fall back to unmasked (never breaks host request).
- **`django_inspector/middleware.py`** — `set_sampled(True)` at request start; `compute_sampling_decision()` after response; `clear_buffer()` if not sampled. `request.inspector_sampled` bool exposed.

### New Tests
- **`tests/test_masking.py`** — 21 tests covering key redaction, nested traversal, tuple/list, depth limit, JWT pattern, CC pattern, extra keys from settings, non-sensitive passthrough.
- **`tests/test_sampling.py`** — 10 tests covering rate=0/1, 5xx always-capture, slow-request capture, `ContextVar` isolation, middleware integration via `RequestFactory`.

## Commits
- `93adfc4` — feat(04-01): add SENSITIVE_KEYS, SAMPLING_RATE, SLOW_REQUEST_THRESHOLD_MS to conf.py
- `76831fc` — feat(04-01): add masking.py — sensitive-data redaction (MASK-01..06)
- `a14bb24` — feat(04-01): hook mask_metadata into BaseWatcher.record()
- `da33a1b` — feat(04-01): add sampling.py + middleware sampling gate (SAMP-01..05)
- `d4dc5db` — test(04-01): add test_masking.py (21 tests) + test_sampling.py (10 tests)

## Verification

- `uv run python -m pytest tests/` → **96 passed, 0 failed**
- Smoke test: `mask_metadata({"password": "s3cr3t"})` → `{"password": "***REDACTED***"}` ✓
- Smoke test: `compute_sampling_decision(req, 500_response)` with `SAMPLING_RATE=0.0` → `True` ✓
- Smoke test: `compute_sampling_decision(req, 200_response)` with `SAMPLING_RATE=0.0` → `False` ✓

## Self-Check: PASSED

All 7 plan tasks executed. All acceptance criteria met. 96/96 tests pass. No pre-existing tests broken.

## Deviations from Plan

**[Rule 1 - Bug Fix] Integration test used Client() instead of RequestFactory.**
Found during: Task 7 | Issue: `tests/settings.py` has no `ROOT_URLCONF` — `Client.get("/")` raised `AttributeError` | Fix: Replaced `Client` tests with `RequestFactory` + direct `InspectorMiddleware` instantiation | Files modified: `tests/test_sampling.py` | Verification: 96 tests pass | Commit: `d4dc5db`

**Total deviations:** 1 auto-fixed. **Impact:** Test quality improved — tests now exercise middleware directly, not through URL routing, making them more focused.

## Requirements Satisfied

- MASK-01: Default `SENSITIVE_KEYS` redacts passwords, tokens, cookies, Authorization ✓
- MASK-02: Nested dict/list/tuple traversal ✓
- MASK-03: CC-shaped value scanning ✓
- MASK-04: JWT-shaped value scanning ✓
- MASK-05: Extra keys from settings are additive ✓
- MASK-06: Depth > 10 truncated to `***REDACTED_DEEP***` ✓
- SAMP-01: `SAMPLING_RATE` setting controls capture fraction ✓
- SAMP-02: Error responses (5xx) always captured ✓
- SAMP-03: Slow requests (`>= SLOW_REQUEST_THRESHOLD_MS`) always captured ✓
- SAMP-04: `SAMPLING_RATE=1.0` (default) captures everything ✓
- SAMP-05: `SAMPLING_RATE=0.0` captures nothing except errors and slow requests ✓
