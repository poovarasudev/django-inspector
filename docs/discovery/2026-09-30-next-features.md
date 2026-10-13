# Next features: product discovery (after 0.2.0)

*2026-09-30. Input for breaking down the next milestone in `docs/roadmap.md`.*

## 1. The opportunity

**Product:** django-inspector 0.2.0. It has seven watchers (request, SQL, exception, cache, template, signal, log), and all of their events for one request share a trace id. The dashboard is built with HTMX. The package has production-safety features (masking, sampling, access control, retention) and adds about 0.6 ms per request at p50.

**Who it's for:** Django developers and tech leads, first in local development and staging, then in production with sampling. QA and support teams are secondary users (PRD §6).

**Objective (assumed; please correct):** grow adoption after 0.2.0 by making the package clearly better than the tools people already use: django-debug-toolbar, django-silk and Sentry (PRD §18).

**Outcomes to move:** PyPI downloads and GitHub stars (PRD §20), less time to debug, and more teams willing to turn it on in production.

### What the code tells us today

These are observations from the code, and they shape the ideas below.

- **A trace only exists inside an HTTP request.** `set_trace_id` is called only from `middleware.py`. Anything outside a request records nothing, because there's no active trace:
  - management commands
  - Celery tasks
  - scripts and cron jobs
  - the shell

  This blocks every "background work" watcher in the PRD, and it also blocks the PRD's main differentiator: linking a request to the tasks it starts.
- **The dashboard shows events, not answers.** Every page is a newest-first list of events of one type. There's no page that groups events, for example:
  - the most frequent exceptions;
  - the slowest query shapes;
  - endpoints ranked by p95.

  The SQL watcher already normalises each query to find N+1 patterns, but it doesn't store that normalised form. As a result, the dashboard can't group queries by it.
- **Traces are flat.** `Event` has `trace_id`, `event_type`, `timestamp` and `metadata`. It has no `span_id` or `parent_span_id` (PRD §10), and no real columns for the fields people filter on (`method`, `status_code`, `path`). So the waterfall can only order events by time; it can't show that one event happened inside another.
- **There's no path from the app back to the trace.** Responses carry no trace-id header, and log lines carry no trace id. A developer who sees a slow request in the browser's devtools can't find its trace.

## 2. Ideas, 5 from each perspective

### Product manager: business value and differentiation

1. **Celery watcher that links requests to tasks.** A task started during a request carries the request's trace id. The worker then records its events into that same trace. This is the PRD's headline differentiator (§18), and neither debug-toolbar nor silk offers it.
2. **Management command watcher (CMD-01).** Each command run (`migrate`, a data backfill, a cron job) becomes its own trace, with its SQL, logs and exceptions attached.
3. **Outgoing HTTP watcher** for `requests` and `httpx`. It records the URL, method, status, latency and retries. Slow third-party APIs are one of the most common reasons a Django request is slow, and today none of that time shows up.
4. **Email watcher with preview and `.eml` export (MAIL-01/02).** Telescope's Mail tab is one of its most-liked features: it makes checking emails during development much easier.
5. **DRF integration.** It records the time spent in serializers, validation and rendering. Most Django APIs use DRF, and much of their time goes to serializers that no current watcher can see.

### Product designer: usability and delight

1. **An "Issues" overview page.** It groups exceptions by fingerprint, groups slow and N+1 queries by query shape, and ranks endpoints by p50, p95 and error rate. Each group links to example traces. This is the page a lead opens every morning.
2. **Trace comparison (DASH-02).** It puts two traces of the same endpoint side by side and highlights the differences: query counts, query shapes and time per watcher. It answers "why was this run slow when that one was fast?"
3. **A trace link in the page and in devtools.**
   - Every traced response gets an `X-Inspector-Trace` header.
   - In `DEBUG`, a small corner badge is added to HTML pages. It links to the trace and shows the query count and time.
4. **Full-text search (DASH-01)** across event data, with filters such as user, status and "has N+1".
5. **Pin, tag and share a trace.** A pinned trace survives `RETENTION_HOURS`, and a tag like `bug-1234` makes it easy to send to a teammate.

### Software engineer: technical leverage and scale

1. **A public tracing API.** It provides `inspector.trace("name")` as both a context manager and a decorator, handles starting and flushing a trace outside the middleware, and adds a logging filter that stamps the trace id on log records. The command, Celery and scheduler watchers are all small once this exists.
2. **Event schema v2.** It adds nullable `span_id` and `parent_span_id` fields for real nesting, plus real columns for `kind`, `method`, `status_code`, `path`, `duration_ms` and `fingerprint`. The schema is public, so these fields should be added before more watchers multiply the number of rows.
3. **N+1 and query-budget guard for tests.** It adds a pytest fixture and a context manager, for example `with inspector.assert_queries(max=5, no_n_plus_one=True)`, and optionally a `DEBUG`-only mode that raises on N+1. It reuses the existing detection to catch regressions in CI, which is PRD §21's "CI performance regression detection".
4. **Watchers from other packages (PLUG-01).** It adds an entry-point group, `django_inspector.watchers`, and a documented, stable `BaseWatcher` API. Kafka, GraphQL or payment-gateway watchers can then live in their own packages.
5. **Writing events in the background (PERSIST-01).** A bounded queue and a daemon thread write the events, with a synchronous fallback, to keep overhead low as the watcher count grows. The benchmark should first show how much of the 0.6 ms is spent in `bulk_create`.

## 3. The top five

I scored each idea on four criteria:

- how well it fits the objective;
- its effect on the outcomes;
- how feasible it is under the constraints: no new runtime dependencies, Python 3.8 or later, and at most about 2 ms p50 overhead;
- how much it differentiates the package from debug-toolbar, silk and Sentry.

### #1: Trace API plus the management command watcher

**What:** a public `inspector.trace()` API to start a trace, plus the first watcher built on it, for management commands. Each command run becomes a trace with its SQL, logs and exceptions attached.

**Why this one:**
- Every background-work feature in the PRD depends on it: commands, Celery, the scheduler and the shell.
- CMD-01 is already on the v1.2 roadmap.
- It's mostly plumbing that already exists: the start-of-request and end-of-request parts of the middleware, moved behind a reusable API.
- It needs no dependencies and is cheap to build.

**Assumptions to check:**
- Users want commands traced by default, or at least find it easy to opt in with a setting such as `COMMAND_WATCH_LIST`. The risk is that `migrate` or `collectstatic` fill the table with noise.
- Buffering events for a long command is acceptable. Flushing every N events, or at the end, needs a cap, because `MAX_EVENTS_PER_TRACE` was designed for requests.
- The dashboard can show a trace that isn't a request without a redesign. The existing `/trace/<id>/` route is the starting point.

**Cheapest test:** hook `BaseCommand.execute` in a scratch project, run a real data backfill, and check whether the resulting trace answers "what did this command actually do?"

### #2: Celery watcher with request-to-task correlation

**What:** a task started during a request carries the request's trace id in its headers. The worker restores that id, so the task's SQL, logs and exceptions appear in the request's trace. It records the task name, the arguments (masked), the queue, retries, the result status and the duration.

**Why this one:**
- It's the differentiator the PRD names (§18), and no tool in the comparison offers it.
- A large share of production Django apps use Celery.
- It's the clearest way to deliver the core value, "every request's full story".

**Assumptions to check:**
- Celery's signals (`before_task_publish`, `task_prerun` and `task_postrun`) are enough to carry the trace id without patching Celery. The watcher must also import Celery lazily, so Celery never becomes a runtime dependency.
- Workers can write to the same database as the web process, which is usually true when they share `settings.py`.
- Users want the task's events **inside** the parent trace, not in a separate trace with a link back. This needs a decision, and parent spans (from event schema v2) make both options possible.

**Cheapest test:** a spike that sends a task from a view and checks that one trace id appears in both the web process and the worker, before any dashboard work.

### #3: Issues overview page

**What:** a new landing page that groups what was captured:
- exceptions by fingerprint (type and location), with a count, when each was first and last seen, and example traces;
- queries by normalised shape, with their count, p95 and how many requests had an N+1;
- endpoints ranked by p50, p95 and error rate.

**Why this one:**
- It turns stored events into answers, which today takes someone paging through lists.
- It's the page a lead reaches for first, so it's what makes people keep using the dashboard after installing it.
- The SQL normalisation already exists. The main new work is storing a `fingerprint` for each query and exception, and writing aggregate queries.

**Assumptions to check:**
- Grouping with SQL over the JSON `metadata` field is fast enough at the row limits we expect on SQLite, PostgreSQL and MySQL, or it needs the real columns from event schema v2. Measure this with the `CappedPaginator` limits.
- A fingerprint of type plus location is stable enough across deploys. Line numbers shift when code changes, so the fingerprint may need a normalised location, such as the function name.
- Sampled data still gives useful rankings. For example, with `SAMPLING_RATE=0.1` the page should show counts as estimates.

**Cheapest test:** write the three aggregate queries against a copy of a real `Event` table, and ask two users whether the result would change what they fix first.

### #4: Outgoing HTTP watcher (`requests` and `httpx`)

**What:** it records the method, the URL (masked), the status, the latency, retries, and optionally the size of the request and response bodies. The calls are shown on the waterfall, and the Issues page ranks slow hosts.

**Why this one:**
- Time spent calling other services is currently invisible, even though it's often the largest part of a slow request.
- It's an easy benefit to explain on the README.

**Assumptions to check:**
- Wrapping the libraries is safe and cheap:
  - `requests.Session.send`;
  - the transports of `httpx.Client` and `httpx.AsyncClient`.

  It must import each library lazily and never add a dependency.
- Masking URLs and headers with the existing rules is enough; things like signed URLs and API keys in the query string might need more.
- `aiohttp` can wait. The PRD lists it, but it's much less common in Django apps.

**Cheapest test:** check how many projects in the likely audience depend on `requests` or `httpx` (a GitHub code search, or asking early users). Then build a prototype that wraps `requests` and measure its overhead with `scripts/benchmark.py`.

### #5: N+1 and query-budget guard for tests

**What:** a `django_inspector.testing` module that adds a context manager, a pytest fixture, and a decorator for `TestCase`:

```python
with assert_queries(max=5, no_n_plus_one=True, no_duplicates=True):
    client.get("/orders/")
```

When the guard fails, its message shows the query shapes involved and where each query was run from. Optionally, the setting `N_PLUS_ONE="raise"` makes N+1s fail loudly during development.

**Why this one:**
- It moves catching N+1s from production to CI. That fits "less time to debug" and PRD §21.
- It reuses the SQL watcher's detection as is, needs no dashboard work, and adds no dependency (the pytest part is loaded only when pytest is installed).
- Features that run in CI keep the package installed in a project, which helps adoption.

**Assumptions to check:**
- Teams don't already get this from `django-assert-num-queries`, `nplusone` or `django-zeal`. We need to test whether showing where each query came from, plus the query shapes, is a strong enough reason to switch.
- The guard must work with the inspector's middleware off, directly around test code. That depends on the trace API from #1.

**Cheapest test:** add it to this repo's own tests and to one real project, and count the N+1s it finds that aren't already known.

## 4. Suggested order and changes to the v1.2 roadmap

**The v1.2 roadmap today:** the Model, Email, Management Command and Middleware watchers.

**Suggested v1.2, "Beyond the request":**

| Phase | Contents | Requirement IDs |
|-------|----------|-----------------|
| 8 | Event schema v2 (spans, columns, fingerprints), the trace API, and the `X-Inspector-Trace` header and log filter | new: TRACE-01..n; schema from the Deferred table |
| 9 | Management command watcher and the test guard (#5) | CMD-01, new: TEST-01..n |
| 10 | Issues overview page (#3) | new: DASH-04; DASH-01 search could come later |
| 11 | Email watcher (a small, well-liked feature to finish the milestone) | MAIL-01, MAIL-02 |

**v1.3:**
- the Celery watcher (#2);
- the outgoing HTTP watcher (#4);
- the scheduler watcher, which is easy once Celery tasks can be traced.

The Redis watcher stays in v1.3 if the cache watcher doesn't already give users enough.

**Move later:**
- **Middleware watcher (MW-01).** Timing each middleware means wrapping every one of them. That adds overhead to every request and has to be done separately for sync and async middleware, and the answer it gives ("the auth middleware took 2 ms") rarely matters. The trace API allows it to be added later as an opt-in debug mode.
- **Model watcher with field changes.** Recording the old and new value of each field needs either an extra read before each save or tracking every model instance from `__init__`, and both are expensive. The signal watcher already records saves and deletes. A lighter "which models were written, and how many times" summary built from existing events might be enough.

## 5. Small improvements worth doing whatever else is chosen

These take under a day each.

- **`X-Inspector-Trace` response header.** Add it when a trace is kept. Only add it for staff users or in `DEBUG`, so trace ids don't leak publicly.
- **Trace id in logs.** A `logging.Filter` that sets `record.inspector_trace_id`, so application logs can be matched to traces.
- **Register `Event` in the Django admin** (read-only, concern C-13).
- **Pin a trace** so retention doesn't delete it. This needs one boolean field, and it can go in the event schema v2 migration.
- **CSV export (DASH-03).** Now that JSON export exists, CSV is only a second output format.
