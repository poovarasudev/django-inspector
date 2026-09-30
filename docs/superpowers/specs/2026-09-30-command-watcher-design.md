# Phase 10 — Management Command Watcher

**Status:** Written under the owner's standing approval (2026-09-30: "proceed with all the upcoming phases … with assumptions"); assumptions are listed at the end for review · **Date:** 2026-09-30 · **Branch:** `feat/v1.2-beyond-the-request`
**Covers:** CMD-01..03 (`docs/requirements.md`), PRD §11.10 · CMD-04 shipped in Phase 9
**Roadmap:** Phase 10 · **Builds on:** Phase 9 trace API (`2026-09-30-trace-api-design.md`)

## Goal

Every run of a chosen management command becomes a trace, and its SQL, logs and exceptions are attached to it. The trace has a `command.completed` anchor carrying:
- the command's name, args and options
- bounded stdout and stderr
- its duration and exit code

Long commands:
- keep memory flat
- write their events in chunks, so a killed command keeps what it already recorded

## Decisions

| Decision | Choice |
|---|---|
| Hook | Patch `django.core.management.base.BaseCommand.execute` on the class at `install_hooks()`, restore it at `remove_hooks()`. It covers `manage.py`, `django-admin` and `call_command`. |
| Opt-in | New setting `COMMAND_WATCH_LIST` (list of command names, default `[]`). A command runs untraced unless its name is listed. |
| Never traced | `runserver`, `runserver_plus`, `testserver`, `test`, `shell`, `shell_plus`, `dbshell`, `migrate`, `inspector_cleanup` are never traced, even when listed. A system check warns when one is listed. |
| Command name | The last segment of the command class's `__module__` (`myapp.management.commands.backfill` → `backfill`), which is the name `manage.py` uses |
| Lifecycle | The Phase 9 `_Run` machinery, generalised with an anchor type, a describe hook and a "record when joined" flag. No second lifecycle. |
| Inside another trace | A listed command run by `call_command` inside a request or `trace()` joins that trace and records its `command.completed` there. It doesn't flush, and it doesn't capture the exception (the outer trace does). |
| Output capture | A bounded tee around the command's real stdout and stderr, passed in as `options["stdout"]`/`["stderr"]`. The first `MAX_OUTPUT_CHARS = 8192` characters of each are kept and masked with `mask_value`. The user still sees all output. |
| Chunked flush | A command trace writes its buffer every `FLUSH_CHUNK_SIZE = 500` events. A trace that reaches a chunk flush is kept regardless of sampling. |
| Bounded query stats | `_QueryStats` stops adding *new* keys after `MAX_TRACKED_QUERIES = 10000` distinct shapes (and separately 10000 distinct exact queries). Counts for keys already tracked keep increasing. This applies to requests too. |
| Watcher default | `"command": True` in `WATCHERS`. It is inert until `COMMAND_WATCH_LIST` names something. |

Alternatives rejected:
- **Patching `run_from_argv`:** misses `call_command`.
- **Patching `OutputWrapper.write` globally:** it would affect every command, traced or not.
- **A `COMMAND_IGNORE_LIST` with trace-everything by default:** noisy, and dangerous for long-lived commands. See the discovery doc.
- **Making chunk size and output size settings:** constants keep the settings surface small. They can become settings later if anyone asks.

## Generalising `_Run` (`django_inspector/tracing/api.py`)

`_Run` gains keyword arguments with defaults that keep `trace()` exactly as it is:

```python
_Run(name, anchor_type="trace.completed", describe=None, record_when_joined=False, chunk_size=None)
```

- **`describe(exc_type, exc_value) -> (failed: bool, extra: dict)`:**
  - The default is `(exc_type is not None, {})`.
  - `extra` is merged into the anchor metadata, after `name`/`duration_ms`/`status`/`error_type`.
  - The command watcher uses it to compute `exit_code` and to treat `SystemExit(0)` as success.
- **Exception capture:** only when `failed` and `issubclass(exc_type, Exception)`, and not when joined. `SystemExit` and `KeyboardInterrupt` are never passed to `capture()`.
- **`record_when_joined=True`:** when joining an active trace, the run records its anchor into the outer trace at exit, stamped with the run's own start time. It doesn't capture, flush or close.
- **`chunk_size`:** passed to `TraceScope.start(chunk_size=...)`.
- **The anchor always carries `events_dropped`** when the buffer dropped events past `MAX_EVENTS_PER_TRACE`, like the request anchor.
- **`_NEVER_DROPPED` gains `"command.completed"`.**

## Chunked flushing (`storage/flush.py`, `tracing/scope.py`)

**`_EventBuffer`** gets:
- `written` (events already flushed from this buffer)
- `chunk_size` (None = never chunk)
- `force_keep` (set once a chunk has been written)

**Flushing and clearing:**
- `flush_events()` removes the written items but keeps `dropped` and adds to `written`, through a new `_EventBuffer.mark_written()`.
- `clear()` (discard) still resets everything. Existing tests on `clear()` keep passing.

**The `MAX_EVENTS_PER_TRACE` check** counts `len(buf) + buf.written`, so the limit applies to the whole trace, not one chunk.

**`buffer_event()`**, after appending: if `buf.chunk_size` is set and `len(buf) >= buf.chunk_size`:
- it calls `flush_events()`, guarded by the usual try/log/raise-on-`INSPECTOR_RAISE_ERRORS`;
- it sets `buf.force_keep = True`.

Chunks are only used by command traces, which are sync, so this never runs in an async context.

**`TraceScope`:**
- `start(trace_id=None, chunk_size=None)` passes `chunk_size` to the new buffer.
- `flush(keep)` treats `keep or buffer.force_keep` as keep. Once a chunk is already in the database, discarding the rest would leave a half trace.

A killed command leaves its flushed chunks and no anchor. The Phase 9 trace page already renders a trace without an anchor, with an "incomplete trace" notice.

## Command watcher (`django_inspector/watchers/command.py`)

`CommandWatcher(BaseWatcher)`, `watcher_name = "command"`, registered and added to `apps.WATCHER_MODULES`.

**Hooks:**
- `install_hooks()` saves `BaseCommand.execute` and replaces it with `_traced_execute`.
- `remove_hooks()` restores it. It never double-patches: a marker attribute guards this.

**`_traced_execute(self, *args, **options)`:**
1. Resolve the name. If the watcher is disabled, the inspector is disabled, the name isn't in `COMMAND_WATCH_LIST`, or it's in `NEVER_TRACED`, call the original and return.
2. Wrap stdout and stderr in `_BoundedTee` objects:
   - the real stream is `options.get("stdout")` or `self.stdout._out`;
   - the wrapped streams are set back into `options`.
3. Run the original inside `_Run(name, anchor_type="command.completed", describe=..., record_when_joined=True, chunk_size=FLUSH_CHUNK_SIZE)`, using the sync enter/exit.
4. Build the anchor extras:
   - `command`: the name
   - `args`: masked list of `repr`-bounded args
   - `options`: masked; options without a JSON-friendly value are `repr`-bounded; `stdout`, `stderr` and anything starting with `_` are dropped
   - `stdout`, `stderr`: captured text, masked
   - `stdout_truncated`, `stderr_truncated`: flags
   - `exit_code`
5. Work out `exit_code`:
   - `0` on success;
   - `CommandError.returncode` (default 1);
   - `SystemExit.code` (`None` → 0; a non-int code → 1);
   - `130` for `KeyboardInterrupt`;
   - `1` for any other exception.

   `failed = exit_code != 0`.
6. Return the original's return value, and re-raise any exception unchanged.

Inspector failures inside the watcher's own code (building metadata, the tee) are logged and never break the command, as in `trace()`.

**`_BoundedTee`:**
- `write(s)` forwards `s` to the real stream, then appends to an in-memory buffer until `MAX_OUTPUT_CHARS` is reached, setting `truncated` when later text is cut.
- Any other attribute (`flush`, `isatty`, `encoding`, …) is delegated to the real stream through `__getattr__`, so `OutputWrapper` styling works unchanged.

## Query stats bound (`watchers/sql.py`)

`_QueryStats.add()`:
- If a normalised shape isn't yet tracked and `len(self.similar) >= MAX_TRACKED_QUERIES`, it isn't added. The returned similar count is 1, so no N+1 flag is raised for untracked shapes.
- The same applies to `exact` with its own cap.

The constant sits next to the existing `MAX_*` constants.

## Settings and checks

| Key | Default | Rule |
|---|---|---|
| `COMMAND_WATCH_LIST` | `[]` | A list of strings (add to `_STRING_LIST_KEYS`). New warning `django_inspector.W009` when a listed name is in `NEVER_TRACED`. |
| `WATCHERS["command"]` | `True` | Existing watcher-map rules |

Both get README rows.

## Dashboard

**Trace types:** `TRACE_ANCHOR_TYPES` gains `"command.completed"`.

**Trace page:** when the anchor is a `command.completed`, `inspector/traces/detail.html` adds a command section:
- the exit-code badge
- args and options as JSON blocks
- stdout and stderr in `<pre>` blocks, with "truncated" notes

**Traces list:**
- It shows the command name for command anchors. `name` is set to the command name in the metadata, so the existing column works.
- It gets a new "Type" filter (`all` / `trace` / `command`) on `event_type`.

## Error handling

- The command's own exceptions always propagate unchanged, and the exit code in the anchor mirrors what `manage.py` returns.
- Inspector failures are logged (or raised under `INSPECTOR_RAISE_ERRORS`, except while a user exception is propagating, as in Phase 9).
- A failing chunk flush is logged. The events stay in the buffer, and the final flush retries them.

## Testing

| File | Covers |
|---|---|
| `tests/test_command_watcher.py` (new) | Lifecycle: install patches `BaseCommand.execute`, remove restores it, no double-patching. An unlisted command records nothing. A listed command run via `call_command` produces a kept trace with a `command.completed` anchor (all fields) plus its SQL and log events under the same trace id. `NEVER_TRACED` names record nothing even when listed. stdout/stderr are captured, masked and truncated, and the user's stream still gets the full output. Exit codes for success, `CommandError(returncode=3)`, `SystemExit(0)`, `SystemExit(2)`, `KeyboardInterrupt` and `ValueError`, with the exception re-raised unchanged. A `ValueError` produces an `exception.raised` event. Joined: `call_command` inside `trace()` and inside a request records `command.completed` in the outer trace without flushing it early. With the inspector disabled, commands run untouched. A failing metadata build doesn't break the command. Test commands live in a `tests/testapp/management/commands/` package added to the test settings. |
| `tests/test_storage.py` | Chunked flush: with `chunk_size=3`, events reach the DB before the trace ends; `written` is counted against `MAX_EVENTS_PER_TRACE`; `dropped` survives a chunk flush; `clear()` still resets everything; `force_keep` keeps the rest of a chunked trace at `SAMPLING_RATE=0` |
| `tests/test_sql_watcher.py` | `_QueryStats` stops tracking new keys at the cap, and counts for existing keys still grow (the cap patched down for the test) |
| `tests/test_checks.py` | `COMMAND_WATCH_LIST` type rule; W009 for a never-traced name |
| `tests/test_dashboard_features.py` | Command trace page shows the exit code, args, options and output; the Traces list shows command traces and filters by type |
| `tests/test_trace_api.py` | `trace()` behaviour unchanged after `_Run` is generalised (the existing tests pass untouched) |

Then the full gates: pytest, ruff, mypy, the benchmark (requests must not regress; the query-stats cap is one `len()` per new key), and the build and distribution check.

## Docs

- README:
  - a "Tracing management commands" section: opt-in, the never-traced list, joining, output capture limits, chunked flushing, the incomplete-trace note;
  - rows for `COMMAND_WATCH_LIST` and `WATCHERS["command"]`.
- CHANGELOG: the 0.3.0 section, citing CMD-01..03.
- Roadmap and requirements: tick Phase 10 and CMD-01..03.

## Assumptions (for the owner's end-of-milestone review)

1. **Opt-in allowlist, empty by default.** No command is traced until the owner lists it.
2. **The never-traced list** is `runserver`, `runserver_plus`, `testserver`, `test`, `shell`, `shell_plus`, `dbshell`, `migrate`, `inspector_cleanup`. `makemigrations`, `collectstatic` and others are allowed if listed.
3. **Joining:** a command run inside another trace joins it and records its anchor there, instead of starting its own trace.
4. **Chunked traces are always kept:** a trace that has flushed a chunk ignores `SAMPLING_RATE`.
5. **Constants, not settings:** 8192 characters of captured output per stream and 500-event chunks.
6. **`MAX_EVENTS_PER_TRACE` still applies to commands** (default 1000). With the default, a backfill keeps its first 1000 events and counts the rest as dropped. Set it to 0 for unlimited, chunked capture.
7. **The query-stats cap (10000 keys) also applies to requests.** No real request comes near it.
