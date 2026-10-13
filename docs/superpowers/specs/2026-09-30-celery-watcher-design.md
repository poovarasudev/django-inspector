# Phase 15 — Celery Watcher

**Status:** Written under the owner's standing approval (2026-09-30); assumptions are listed at the end for review · **Date:** 2026-09-30 · **Branch:** `feat/v1.2-beyond-the-request`
**Covers:** CELERY-01..05 (`docs/requirements.md`), PRD §11.12
**Roadmap:** Phase 15 (v1.3) · **Builds on:** Phase 8 (`parent_trace_id`), Phase 9 (`_Run`), Phase 10 (chunked flush, joined anchors)

## Goal

Every Celery task run becomes its own trace, linked to the trace that sent it:
- A request that sends a task records a `task.sent` event.
- The worker records the task's SQL, logs and exceptions under a new trace, anchored by `task.completed`, whose `parent_trace_id` points back to the request.
- The dashboard links the two both ways.

Celery is never a dependency.

## Decisions

| Decision | Choice |
|---|---|
| Hooks | Celery signals only, with no patching of Celery classes: `before_task_publish`, `task_prerun`, `task_failure`, `task_retry`, `task_postrun` |
| Propagation | `before_task_publish` adds the header `inspector_trace_id` when a trace is active |
| Linking | Linked traces, not nested ones (decided for the milestone). The `task.completed` anchor has `parent_trace_id` set, and the sender's trace gets a `task.sent` event. |
| Sampling | Each task trace is sampled on its own with `should_keep` (failures and slow runs always kept). A link can point to a parent that wasn't kept; the page says so. |
| Eager mode | With `task_always_eager`, the task runs inline inside the sender's trace. It joins that trace (`record_when_joined`) and records `task.completed` there. |
| Lifecycle | The Phase 9/10 `_Run`, entered in `task_prerun` and exited in `task_postrun`, with `chunk_size=FLUSH_CHUNK_SIZE` as for commands |
| Lazy import | `install_hooks()` imports `celery.signals` inside a `try`. Without Celery, the watcher enables as a no-op. |
| Default | `"celery": True` (inert without Celery) |
| Extra | `django-inspector[celery]` = `celery>=5.2`, for docs and pinning only. The code never needs it. |

Alternatives rejected:
- **Wrapping `Task.__call__` or `apply_async`:** patches Celery internals that change between versions, when signals are the supported interface.
- **Nesting the task's events inside the request trace:** the task is sent before the request's keep/drop decision, so its events would be orphaned when the request is dropped. See the milestone decision.

## Watcher (`django_inspector/watchers/celery.py`)

`CeleryWatcher(BaseWatcher)`, `watcher_name = "celery"`, added to `apps.WATCHER_MODULES`, with `WATCHERS["celery"] = True`.

**Name clash.** The module is named `celery.py` inside the package, and the code uses `from celery import signals` inside functions. Absolute imports make this safe. If a tool trips on the name, rename it `celery_watcher.py` and note it.

**`install_hooks()`** connects the five signal handlers with `weak=False` and a `dispatch_uid`. **`remove_hooks()`** disconnects them.

### Sending side (`before_task_publish(sender, headers, body, routing_key, exchange, properties, **kw)`)

- If no trace is active, it does nothing.
- Otherwise:
  - It sets `headers["inspector_trace_id"] = get_current_trace_id()`, even when detail sampling skipped this trace, since the task trace is sampled on its own and should still link back.
  - When the watcher is capturing (`is_capturing()`), it records `task.sent` with: `task` (the `sender`, i.e. the task name), `task_id` (`headers["id"]`), `queue`/`routing_key`, `exchange`, `eta`, `countdown` when present, and `retries` (`headers["retries"]`).
- Guarded: a failure logs and never blocks publishing.

### Worker side

**Run state.** A module-level dict holds the open runs: `_runs: Dict[str, _TaskRun]` keyed by `task_id`, with a size cap (`MAX_OPEN_TASK_RUNS = 1000`, oldest evicted, logged). This state sits beside the ContextVar trace set by `_Run.enter()`:
- prerun and postrun run in the same thread/greenlet as the task body with the prefork, threads, solo, gevent and eventlet pools, so the ContextVar is visible to the task's watchers;
- the dict carries per-task data between the separate signal calls.

**`task_prerun(task_id, task, args, kwargs, **kw)`:**
1. Read `parent = getattr(task.request, "inspector_trace_id", None) or (getattr(task.request, "headers", None) or {}).get("inspector_trace_id")`. Protocol-2 custom headers end up on the request; the worker test confirms which path Celery 5 uses.
2. Create `_Run(name=task.name, anchor_type="task.completed", describe=..., record_when_joined=True, chunk_size=FLUSH_CHUNK_SIZE, parent_trace_id=parent)` and `enter()` it.
3. Store it in `_runs[task_id]` with the start time, masked bounded `args`/`kwargs`, `retries` (`task.request.retries`), `hostname`, `queue` (`task.request.delivery_info.get("routing_key")`) and `eta`.

**`task_failure(task_id, exception, traceback, **kw)`:** store `(type(exception), exception, traceback)` on the run for postrun.

**`task_retry(request, reason, **kw)`:** mark the run `retrying=True`.

**`task_postrun(task_id, task, retval, state, **kw)`:**
1. Pop the run.
2. Exit it with the stored exc info, or `(None, None, None)`. `describe` returns:
   - `failed = state in ("FAILURE",)`, or an exception is stored and the run isn't retrying;
   - extras: `task`, `task_id`, `state` (`RETRY` when retrying), `retries`, `worker`, `queue`, `eta`, `args`, `kwargs`, `result` (masked `repr`, 500 characters, success only), `exception` (`"Type: message"`, masked).
3. Exception capture follows the Phase 10 rule (only `Exception` subclasses; not when joined), so a task failure produces an `exception.raised` event in the task trace.

All handlers are guarded. Inspector failures log (or raise under `INSPECTOR_RAISE_ERRORS`), and a failing handler can never fail or retry the task.

### `_Run` and flush additions

- `_Run` gains `parent_trace_id=None`, used only for the anchor.
- `buffer_event(..., parent_trace_id=None)` stores it on the buffered item, and `flush_events()` writes it to the Phase 8 `parent_trace_id` column (`_safe_columns` leaves it alone).
- Only the anchor carries it. Children are found with `Event.objects.filter(parent_trace_id=<trace>, event_type="task.completed")`.
- `_NEVER_DROPPED` gains `task.completed`.

## Dashboard

**Trace types.** `TRACE_ANCHOR_TYPES` gains `task.completed`. The Traces list type filter gains "task".

**Trace page, task section** (anchor `task.completed`):
- state badge, task id, retries, worker, queue, ETA, args/kwargs, result or exception;
- "Sent by" linking to `/trace/<parent_trace_id>/`, or "sent by trace <id> (not stored)" when the parent trace has no events.

**Request and trace pages, "Tasks sent" section:**
- It lists the trace's `task.sent` events: task name, queue, ETA.
- Each links to its task trace when one exists. The lookup is a single query: `task.completed` anchors with `parent_trace_id=<this trace>`, matched on `metadata.task_id` in Python.
- Otherwise it shows "not run yet, or not kept".

## Error handling

- Publishing and task execution are never affected by the watcher.
- A missing `postrun` (a worker killed mid-task) leaves the chunks already flushed. The capped run dict evicts the stale entry later, and the trace shows as incomplete (Phase 9 notice).
- Handlers tolerate missing kwargs across Celery versions by reading with `.get`.

## Testing

`celery>=5.2` is added to the `dev` extra. Tests that need it use `pytest.importorskip("celery")`.

| File | Covers |
|---|---|
| `tests/test_celery_watcher.py` (new) | **Unit:** each handler called directly with the signal kwargs Celery sends (headers dict, fake task objects with a `request`): header injection only inside a trace; `task.sent` fields; a prerun/postrun pair produces a kept task trace with its anchor, `parent_trace_id` and all extras; failure → `FAILURE`, an `exception.raised` event and an always-kept trace; retry → `RETRY`; eager (prerun inside an active trace) joins and records the anchor in the outer trace; the open-run cap evicts; a handler failure doesn't raise into Celery; no Celery installed (simulated) → watcher enables as a no-op. **Integration:** a Celery app with the in-memory broker (`memory://`) and `cache+memory://` backend, `celery.contrib.testing.worker.start_worker` in a thread, a task sending a SQL query and a log. A view (through the test client) calls `.delay()`. Assert the request trace has `task.sent`, a separate task trace exists with `parent_trace_id` = the request's trace id, and the task's SQL/log events are under the task trace. Needs `transaction=True`. |
| `tests/test_storage.py` | `parent_trace_id` written from `buffer_event` to the column |
| `tests/test_dashboard_*.py` | Task trace page section and parent link (stored and missing parent); request page "Tasks sent" with and without a stored child; Traces list type filter "task" |

Then the full gates. `check_dist` is unchanged; the extra is metadata only.

## Docs

- README: a "Celery" section (setup is just having both apps configured; linked traces; sampling independence; eager mode; what's stored; the `[celery]` extra) and the `WATCHERS["celery"]` row.
- CHANGELOG: the 0.4.0 (unreleased) section, citing CELERY-01..05.
- Roadmap and requirements: tick Phase 15 and CELERY-01..05; mark v1.3 complete.

## Assumptions (for the owner's end-of-milestone review)

1. **Linked traces, sampled independently.** A task's parent trace may not be stored.
2. **Eager tasks (`task_always_eager`) join the sender's trace** instead of starting their own.
3. **Only the task anchor carries `parent_trace_id`,** not every event in the task trace.
4. **Task results are stored as a masked `repr`,** capped at 500 characters, for successful tasks only.
5. **The Celery watcher is on by default** and does nothing when Celery isn't installed.
6. **The `[celery]` extra exists** for documentation and version pinning only.
