# Python Catalogue

## 1. Reflection on Owned Types

### Owned attribute hedging

**Pattern Name & Default Grade Hint:** Owned attribute hedging — `[Simplify]`, **Revalidation**.

**Signal (Bad):** A known local type is treated as a partially initialized dynamic object.

```python
def read_total(reader):
    if not hasattr(reader, "totals"):
        reader.totals = {}
    return getattr(reader, "count", 0)
```

**1-Hop Verify & Fix (Good):** Inspect the receiver's constructor/dataclass and the immediate producer: are `totals` and `count` part of its owned contract? Initialize them at construction and read them directly, keeping real optional *values* distinct from missing attributes.

```python
class Reader:
    def __init__(self):
        self.totals = {}
        self.count = 0


def read_total(reader: Reader):
    return reader.count
```

**Counterweight (When to Keep):** A dynamic proxy, plugin, or externally owned object may genuinely omit an optional capability. An optional protocol member or versioned third-party API can require reflection; a same-file attribute with the same name alone proves nothing about that receiver.

### Required model fields disguised as optional or rechecked at runtime

**Pattern Name & Default Grade Hint:** Required-field reflection and type revalidation — `[Simplify]`, **Revalidation**.

**Signal (Bad):** An admission boundary guarantees a non-null typed field, but a partial internal `SimpleNamespace`, `Optional` field annotation, or runtime `is None` / `isinstance` guard treats the established contract as untrusted.

```python
from types import SimpleNamespace


def project(accepted: AcceptedItem):
    if accepted is None or not isinstance(accepted, AcceptedItem):
        return None
    item = SimpleNamespace(code=accepted.code)
    return describe(item)


def describe(item):
    return getattr(item, "category", None)
```

**1-Hop Verify & Fix (Good):** Inspect the admitted model's definition and immediate callers. When the boundary guarantees `AcceptedItem` and its `category` field, pass the full model directly, align internal annotations with the non-optional contract, and remove the redundant `is None` / `isinstance` / `getattr` guards and unreachable missing-field counters.

```python
def project(accepted: AcceptedItem) -> str:
    return describe(accepted)


def describe(item: AcceptedItem) -> str:
    return item.category
```

**Counterweight (When to Keep):** Sparse external payloads, public partial-update models, `Union` / `Optional` / `Any` inputs, and versioned plugin records can legitimately omit fields or vary by runtime type. Verify the upstream admission guarantee before tightening annotations or removing runtime type checks.

### Required protocol methods probed for incomplete stubs

**Pattern Name & Default Grade Hint:** Test-stub capability fallback — `[Hard]`, **Half-Moves & Zombie Scaffolding**, when it masks a broken test double and sustains a dead production path.

**Signal (Bad):** All production implementations provide the protocol method; incomplete test stubs alone exercise the `getattr` fallback and its derived error codes or counters.

```python
def publish(sender, message):
    emit = getattr(sender, "emit", None)
    if emit is None:
        return {"sent": False, "unsupported": True}
    return emit(message)
```

**1-Hop Verify & Fix (Good):** Inspect the governing protocol and concrete production implementations. When every production implementation defines `emit`, call `sender.emit(message)` directly, complete the stub's method, and delete fallback-only diagnostics.

```python
def publish(sender: Sender, message):
    return sender.emit(message)


class StubSender:
    def emit(self, message):
        return {"sent": True}
```

**Counterweight (When to Keep):** An open plugin protocol with optional hooks, a dynamic adapter, or a documented cross-version consumer can legitimately omit the capability. Preserve capability negotiation when that contract is live.

## 2. Fixed-Shape Dicts vs. Discriminated Unions

### Fixed-key internal dict `.get()` hedging and fake sentinel caps

**Pattern Name & Default Grade Hint:** Fixed-shape dict `.get()` hedging — `[Simplify]`, **Revalidation** (`[Hard]`, **Error Washing**, when a missing key silently falls back to stale state or hides a missing SQL projection column).

**Signal (Bad):** Code reads a fixed-shape dict or row mapping just produced by an owned helper via `.get(key, default)`, falls back to stale state on a missing required key, or fabricates a sentinel upper bound (`start + 30`) before calling a window helper that already clamps against the same `start + 30` limit.

```python
def format_summary(metric: dict, previous_sequence: list[str]) -> dict:
    fake_due = metric.get("due_day") or (metric["start_day"] + 30)
    window_end = clamp_thirty_day_window(metric["start_day"], fake_due)
    sequence = metric.get("sequence") or previous_sequence
    return {"score": metric.get("score", 0.0), "window_end": window_end, "sequence": sequence}
```

**1-Hop Verify & Fix (Good):** Inspect the immediate dict producer, `SELECT` projection, and `clamp_thirty_day_window`. When the producer unconditionally populates `"score"`, `"due_day"` (`int | None`), and `"sequence"`, and `clamp_thirty_day_window` already owns the `start_day + 30` cap when `due_day is None`, index `metric["key"]` directly so missing keys fail fast (fixing any incomplete test fixture or divergent SQL query at its source) and pass `metric["due_day"]` directly.

```python
def format_summary(metric: dict) -> dict:
    return {
        "score": metric["score"],
        "window_end": clamp_thirty_day_window(metric["start_day"], metric["due_day"]),
        "sequence": metric["sequence"],
    }
```

**Counterweight (When to Keep):** Unvalidated external JSON/form payloads, sparse patch dictionaries, `TypedDict(..., total=False)` structures, and legacy archive records with optional keys require `.get()` or explicit key-presence checks.

### Unnarrowed variant dicts vs. discriminated-union narrowing

**Pattern Name & Default Grade Hint:** Unnarrowed variant-dict `.get()` — `[Hard]`, **Error Washing** (or `[Simplify]`, **Revalidation**, when variant narrowing is already established).

**Signal (Bad):** A polymorphic dict varies by an authoritative discriminant key (`kind == "tcp" | "unix"`), and downstream code either papers over variant-specific keys with `.get("port")` or indexes `endpoint["port"]` without narrowing on `endpoint["kind"]` first.

```python
def changed_tcp_port(before: dict, after: dict) -> bool:
    return before.get("port") != after.get("port")
```

**1-Hop Verify & Fix (Good):** Check the producer's variant contract. Narrow on the authoritative discriminant field first, then index the variant's required keys directly.

```python
def changed_tcp_port(before: dict, after: dict) -> bool:
    if before["kind"] != "tcp" or after["kind"] != "tcp":
        return False
    return before["port"] != after["port"]
```

**Counterweight (When to Keep):** If `"port"` is genuinely optional even within the `"tcp"` variant according to the producer schema, check `"port" in after` or `after.get("port")` after narrowing on `after["kind"] == "tcp"`.

## 3. Import-Time Static Self-Checks & Lookup Maps

### Import-time static literal and unreachable-default self-checks

**Pattern Name & Default Grade Hint:** Static literal self-check — `[Simplify]`, **Revalidation** / **Dead Plumbing**.

**Signal (Bad):** A module defines a hardcoded constant table and immediately runs an import-time validator over its own literal, or maintains a full whitelist/range validator around a config sub-dict that no producer ever populates (repeatedly validating its own hardcoded defaults).

```python
_COLUMN_RULES = {
    "code": {"type": str, "required": True},
    "weight": {"type": int, "required": True},
}


def _verify_column_rules() -> None:
    for name, rule in _COLUMN_RULES.items():
        if not isinstance(name, str) or "type" not in rule:
            raise RuntimeError(f"Invalid column rule: {name}")


_verify_column_rules()
```

**1-Hop Verify & Fix (Good):** Check whether `_COLUMN_RULES` or the config sub-dict is ever populated or mutated from external input. When it is a static literal owned by the module (or a ghost config section with zero producers), delete the runtime self-check and return static defaults directly.

```python
_COLUMN_RULES = {
    "code": {"type": str, "required": True},
    "weight": {"type": int, "required": True},
}
```

**Counterweight (When to Keep):** Startup validation of external environment variables, user-supplied config files, database schema versions, or dynamically discovered third-party plugins checks an external trust boundary and must be kept.

### Repeated linear scan over static specification tables

**Pattern Name & Default Grade Hint:** Repeated static-table linear scan — `[Simplify]`, **Redundant Churn**.

**Signal (Bad):** Every lookup linearly scans an immutable module-level tuple or rebuilds a temporary dictionary instead of indexing a prebuilt map.

```python
_HEADER_SPECS = (
    HeaderSpec("code", str),
    HeaderSpec("quantity", int),
    HeaderSpec("due_date", str),
)


def get_header_spec(name: str) -> HeaderSpec:
    by_name = {spec.name: spec for spec in _HEADER_SPECS}
    return by_name[name]
```

**1-Hop Verify & Fix (Good):** Verify that `_HEADER_SPECS` is fixed at import time. Prebuild an $O(1)$ module-level lookup dictionary once when the module loads.

```python
_HEADER_SPECS = (
    HeaderSpec("code", str),
    HeaderSpec("quantity", int),
    HeaderSpec("due_date", str),
)
_HEADER_BY_NAME = {spec.name: spec for spec in _HEADER_SPECS}


def get_header_spec(name: str) -> HeaderSpec:
    return _HEADER_BY_NAME[name]
```

**Counterweight (When to Keep):** Keep runtime iteration when the collection is mutated dynamically at runtime or when a 2–3 item tuple is checked via `name in ("a", "b")` where a separate map adds no value.

## 4. Broad Internal Exception Washing & Hook Rejection Erasure

### Broad or migrated internal exception washing

**Pattern Name & Default Grade Hint:** Internal exception washing / **Moved Defense** — `[Hard]`, **Error Washing** (and **Half-Moves & Zombie Scaffolding** when relocated into callees during a refactor).

**Signal (Bad):** Internal typed computation is wrapped in `except Exception:` or a multi-exception tuple (`KeyError`, `TypeError`, `AttributeError`, `ValueError`, `OverflowError`) that returns a fallback value or gets copy-pasted/migrated into downstream constructors and helpers when an upstream catch is removed.

```python
def compute_allocation(plan: ValidatedPlan) -> dict[str, int]:
    try:
        return {item.id: item.units * plan.rate for item in plan.items}
    except (DomainError, KeyError, TypeError, ValueError, OverflowError):
        return {}
```

**1-Hop Verify & Fix (Good):** Inspect `ValidatedPlan` and the immediate callers (including any surviving caller that lost the catch in the same diff). Internal `KeyError`, `TypeError`, and `AttributeError` indicate code defects, not recoverable runtime conditions. Remove the internal catch so bugs surface immediately; confine `try/except` to exact external boundary operations.

```python
def compute_allocation(plan: ValidatedPlan) -> dict[str, int]:
    return {item.id: item.units * plan.rate for item in plan.items}
```

**Counterweight (When to Keep):** Parsing untrusted wire/file input with exact exception types (such as `json.JSONDecodeError` or `ValueError` around a single `int(raw_text)` call), top-level worker/request isolation boundaries that log full tracebacks and return structured failure responses, or best-effort shutdown cleanup where the contract requires logging and continuing.

### `json.loads(..., object_pairs_hook=...)` washed under `except ValueError`

**Pattern Name & Default Grade Hint:** Hook rejection diagnostic erasure — `[Hard]`, **Error Washing**.

**Signal (Bad):** `json.loads` uses an `object_pairs_hook` that raises a `ValueError`-subclassed domain exception on duplicate or forbidden keys, while the enclosing `try` catches `ValueError` or `Exception` (instead of `json.JSONDecodeError`) and returns a fallback or raises a generic parse error that erases the hook's specific rejection message.

```python
def parse_payload(raw: str) -> dict:
    try:
        return json.loads(raw, object_pairs_hook=reject_duplicate_keys)
    except (ValueError, TypeError) as exc:
        raise InputRejected("invalid_json", "Payload cannot be parsed") from exc
```

**1-Hop Verify & Fix (Good):** Inspect `reject_duplicate_keys` (1 hop). When the hook raises `InputRejected` (which subclasses `ValueError`) with a specific duplicate-key diagnostic, catch `json.JSONDecodeError` (or re-raise `InputRejected` before `ValueError`) so the hook's diagnostic reaches the caller intact.

```python
def parse_payload(raw: str) -> dict:
    try:
        return json.loads(raw, object_pairs_hook=reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise InputRejected("invalid_json", "Payload cannot be parsed") from exc
```

**Counterweight (When to Keep):** When `json.loads` is called without custom hooks (or the hook raises `KeyError` and the caller intentionally normalizes all syntax errors into one contract-specified error code without distinct hook diagnostics), catching `json.JSONDecodeError` / `TypeError` at the parse boundary is appropriate.

## 5. Premature Defaults & Corrupt-State `None` Sentinels

### Premature default shadowing a required-value guard

**Pattern Name & Default Grade Hint:** Default-shadowed validation — `[Hard]`, **Error Washing**.

**Signal (Bad):** A lookup applies a non-`None` fallback via `or` (making `if val is None:` unreachable) or `.get(k, default)` (shadowing missing keys unless `None` is explicitly stored), or calls a parser helper that returns a default string on missing input, and the following lines check `if val is None:` or `if not val:` to reject missing keys (**Lossy Coalescing**).

```python
def parse_shift(row: dict) -> tuple[str, str]:
    opens_at = row.get("opens_at", "08:00")
    closes_at = row.get("closes_at") or "17:00"
    if opens_at is None:
        raise ValidationError("opens_at is required")
    if closes_at is None:
        raise ValidationError("closes_at is required")
    return opens_at, closes_at
```

**1-Hop Verify & Fix (Good):** Check the input contract for `row`. When `"opens_at"` and `"closes_at"` are required keys, validate raw un-defaulted values first and apply defaults only to genuinely optional fields after required checks pass.

```python
def parse_shift(row: dict) -> tuple[str, str]:
    opens_at = row.get("opens_at")
    closes_at = row.get("closes_at")
    if opens_at is None or closes_at is None:
        raise ValidationError("opens_at and closes_at are required")
    return opens_at, closes_at
```

**Counterweight (When to Keep):** When an omitted `"opens_at"` key legitimately defaults to `"08:00"` while an explicit `{"opens_at": None}` is invalid and must be rejected, `opens_at = row.get("opens_at", "08:00")` followed by `if opens_at is None:` is reachable for explicit `None` and should be kept.

### Corrupt context downgraded to `None` or conflated with omitted-argument `None` sentinel

**Pattern Name & Default Grade Hint:** Corrupt-state `None` conflation — `[Hard]`, **Error Washing**.

**Signal (Bad):** Corrupt context is downgraded to `None` (`batch = None if problem else load_batch(ctx)`), bypassing downstream guards (`if batch and ...: raise`), or a function uses `param=None` as its omitted-argument sentinel while callers pass a deserialized stored field that can be `None` when storage is corrupt—silently triggering recomputation instead of rejecting corrupt storage.

```python
def prepare_execution(items: list[dict], archived_decisions=None):
    if archived_decisions is None:
        archived_decisions = classify_items(items)
    return run_with_decisions(items, archived_decisions)


def execute_archived_job(job_record: dict):
    return prepare_execution(job_record["items"], archived_decisions=job_record["decisions"])
```

**1-Hop Verify & Fix (Good):** Inspect all 1-hop callers. If one caller omits the parameter while another passes stored data (`job_record["decisions"]`) where `null`/`None` means storage corruption—or if a prerequisite check detected `problem`—fail fast on the corrupt state and use a module-private `object()` sentinel for omitted arguments.

```python
_COMPUTE_FRESH = object()


def prepare_execution(items: list[dict], archived_decisions=_COMPUTE_FRESH):
    if archived_decisions is _COMPUTE_FRESH:
        archived_decisions = classify_items(items)
    elif archived_decisions is None:
        raise StorageCorruptionError("Archived decisions cannot be null")
    return run_with_decisions(items, archived_decisions)
```

**Counterweight (When to Keep):** When `None` is a documented, valid domain meaning equivalent to omitting the argument (for example, an optional filter where both omitted `tag` and `tag=None` mean "no filter"), a `None` default sentinel is clean and idiomatic.

## 6. Falsy Coalescing & Pre-Validation Equality Diffing

### Falsy `or` coalescing over valid zero, empty, or `False` values

**Pattern Name & Default Grade Hint:** Lossy falsy coalescing — `[Hard]`, **Error Washing** (**Semantic Fidelity** breach).

**Signal (Bad):** An `or` fallback (`cfg.get("timeout") or 30`, `metrics.get("pending_count") or len(scan_all())`) overwrites legitimate `0`, `0.0`, `""`, `[]`, or `False` domain values with a non-zero default or forces a full cache-miss recomputation whenever the cached result is `0` or `[]`.

```python
def resolve_pending_summary(cfg: dict, metrics: dict, all_items: list) -> tuple[int, int]:
    timeout = cfg.get("timeout") or 30
    pending = metrics.get("pending_count") or len([x for x in all_items if not x.done])
    return timeout, pending
```

**1-Hop Verify & Fix (Good):** Check the domain of the left-hand expression. When `0`, `""`, `[]`, or `False` is a valid value or cached result, test `is not None` (or `key in mapping`) explicitly so **Semantic Fidelity** is preserved.

```python
def resolve_pending_summary(cfg: dict, metrics: dict, all_items: list) -> tuple[int, int]:
    raw_timeout = cfg.get("timeout")
    timeout = raw_timeout if raw_timeout is not None else 30
    cached = metrics.get("pending_count")
    pending = cached if cached is not None else len([x for x in all_items if not x.done])
    return timeout, pending
```

**Counterweight (When to Keep):** When empty strings or empty containers are contractually equivalent to missing input at a normalization boundary (e.g., stripping optional blank form text `raw.strip() or None` where `""` means "unset"), `or` coalescing matches the domain rule.

### Pre-validation `==` diffing (`True == 1`) and lax integer guards

**Pattern Name & Default Grade Hint:** Pre-validation boolean/numeric conflation — `[Hard]`, **Error Washing** (**Semantic Fidelity** breach).

**Signal (Bad):** Raw input fields are filtered against existing values with `v != current[k]` *before* type validation—allowing `True` / `False` to pass as unchanged `1` / `0` (`True == 1` and `False == 0` in Python)—or integer parsers use `str.isdigit()` (which accepts Unicode superscripts like `"²"`), `int(Decimal("1.5"))` truncation, `default=0` on `NOT NULL` storage columns, or `isinstance(v, int)` without excluding `bool`.

```python
def changed_numeric_fields(incoming: dict, current: dict) -> dict:
    changed = {k: v for k, v in incoming.items() if v != current[k]}
    for k, v in changed.items():
        if not isinstance(v, int) or isinstance(v, bool):
            raise ValidationError(f"Field {k} must be an integer")
    return changed
```

**1-Hop Verify & Fix (Good):** Inspect the immediate input producer and schema definition: verify whether `incoming` arrives unvalidated from an external boundary, whether booleans are forbidden in the numeric domain, and whether the storage column is `NOT NULL`. Validate or normalize types before diffing (or include `type(v) is type(current[k])` in pre-validation comparisons), validate exact integers with `isinstance(v, int) and not isinstance(v, bool)` (and `text.isdecimal()` for decimal strings), and omit `default=0` on `NOT NULL` storage integer parsers.

```python
def changed_numeric_fields(incoming: dict, current: dict) -> dict:
    for k, v in incoming.items():
        if not (isinstance(v, int) and not isinstance(v, bool)):
            raise ValidationError(f"Field {k} must be an integer")
    return {k: v for k, v in incoming.items() if v != current[k]}
```

**Counterweight (When to Keep):** After both sides have already been parsed into the same canonical Python type at the boundary, plain `==` / `!=` comparison is exact and requires no extra `type(...)` check.

## 7. Linter-Evading Dead Parameters & Ubiquitous `=None` Chains

### Linter-evading dead parameters (`_ = param`, entry overwrite, dual-parameter fallback)

**Pattern Name & Default Grade Hint:** Linter-evading dead parameter — `[Simplify]`, **Dead Plumbing**.

**Signal (Bad):** A function silences unused-argument checks via `_ = param`, overwrites a parameter in its entry prefix before reading it (`metrics = prepared["metrics"]`), or keeps an old parent container alongside a new pre-extracted slice parameter with an unreachable fallback (`if item_slice is None: item_slice = parent_map.get(item.id)`).

```python
def score_item(item, parent_timelines, prepared, item_timeline=None, audit_count=0):
    _ = audit_count
    parent_timelines = prepared["timelines"]
    if item_timeline is None:
        item_timeline = parent_timelines.get(item.id)
    return len(item_timeline)
```

**1-Hop Verify & Fix (Good):** Check all 1-hop callers. When callers already supply `item_timeline` (or `prepared`) and never rely on the overwritten/discarded parameters, make the consumed input required and remove the dead parameters across the signature and call sites.

```python
def score_item(item_timeline: list) -> int:
    return len(item_timeline)
```

**Counterweight (When to Keep):** Framework callbacks, event handlers, `@abstractmethod` / `Protocol` implementations, or public APIs with positional signature obligations may have unused parameters dictated by an external interface; prefer naming them `_unused` when the interface cannot be narrowed.

### Ubiquitous `context=None` fallback chains across internal callees

**Pattern Name & Default Grade Hint:** Pseudo-optional context fallback chain — `[Simplify]`, **Dead Plumbing** (`[Hard]`, **Half-Moves & Zombie Scaffolding**, when the fallback keeps dead repository methods alive).

**Signal (Bad):** Every production entry point constructs a transaction/read context and passes it down, yet internal helpers declare `context=None` and repeat `context.items if context is not None else ItemRepository(conn).list_items()` across layers.

```python
def resolve_references(conn, plan_id: str, context: ReadContext | None = None):
    repo = context.references if context is not None else ReferenceRepository(conn)
    tasks = (
        context.tasks_by_plan(plan_id)
        if context is not None
        else BaselineRepository(conn).raw_tasks_by_plan(plan_id)
    )
    return repo.match(tasks)
```

**1-Hop Verify & Fix (Good):** Inspect all production callers of `resolve_references`. When every production entry point passes a non-null `ReadContext`, tighten the parameter to `context: ReadContext`, delete the `if context is not None else ...` fallback branches, and remove any repository methods whose production call count drops to zero.

```python
def resolve_references(plan_id: str, context: ReadContext):
    return context.references.match(context.tasks_by_plan(plan_id))
```

**Counterweight (When to Keep):** Keep an optional `context=None` parameter on a public entry point or CLI helper that is genuinely invoked both standalone and inside an existing batch context by live callers.

## 8. Hidden State Smuggling & Runtime Test-Hook Trampolines

### Cross-object state and dependency smuggling

**Pattern Name & Default Grade Hint:** Hidden state or dependency smuggling — `[Hard]`, **Hidden Flow**.

**Signal (Bad):** Code stashes private `_attr` fields onto a foreign service/snapshot object and probes them downstream with `getattr(..., None)`, sets `self.active_tokens = ...` or `repo.revisions = ...` inside a `try` block and resets it to `None` in `finally:`, attaches extra functions/maps as attributes on a function object (`fn.prepared = ...`), or mutates `facts["reader"] = reader` to smuggle a database reader into a pure calculation dict.

```python
class ProjectionRunner:
    def run(self, service, snapshot):
        service._cached_window = snapshot.window
        project_rows.prepared = project_rows_prepared
        try:
            self.active_tokens = snapshot.tokens
            payload = {"rows": snapshot.rows, "reader": service}
            return project_rows(payload)
        finally:
            self.active_tokens = None
```

**1-Hop Verify & Fix (Good):** Inspect the producer and consumer functions. Pass required data and dependencies as explicit typed parameters or return values on a dataclass/tuple, complete database reads before entering pure projection functions, and remove transient attribute mutations.

```python
class ProjectionRunner:
    def run(self, snapshot: Snapshot) -> ProjectionResult:
        return project_rows(
            rows=snapshot.rows,
            window=snapshot.window,
            tokens=snapshot.tokens,
        )
```

**Counterweight (When to Keep):** Standard descriptor protocols (`__set_name__`), decorators setting metadata on the wrapper function they construct and return, and context managers managing their own internal lock/connection state (`self._depth`) are legitimate local ownership patterns.

### Production monkeypatch trampolines, `_DEFAULT_*` hook checks, and dropped mode flags

**Pattern Name & Default Grade Hint:** Runtime test-hook trampoline & mock accommodation — `[Hard]`, **Half-Moves & Zombie Scaffolding** / **Error Washing**.

**Signal (Bad):** A facade module runs `_sync_runtime_hooks()` on every call to copy monkeypatched attributes into submodule globals, branches on `if fn is not _DEFAULT_FN:` in production to detect test monkeypatching, drops `kwargs.pop("strict_mode", False)` in an intermediate dispatcher so downstream `strict_mode: bool = False` defaults permanently run the lenient path, or wraps a callback in `except Exception: return result, set()` to swallow `MagicMock` tuple-unpacking `ValueError`s.

```python
_DEFAULT_STOP_HOOK = stop_worker


def _sync_runtime_hooks() -> None:
    _worker_mod.stop_worker = stop_worker


def execute_step(step: Step, callback, **kwargs) -> tuple[StepResult, set[str]]:
    _sync_runtime_hooks()
    _ = bool(kwargs.pop("strict_mode", False))
    if stop_worker is not _DEFAULT_STOP_HOOK:
        stop_worker(step)
    try:
        result, blocked = callback(step, **kwargs)
    except Exception:
        return StepResult.empty(step.id), set()
    return result.finalize(), blocked
```

**1-Hop Verify & Fix (Good):** When runtime test-hook synchronization, `_DEFAULT_*` identity checks, or mock-accommodating catches appear, co-load [`references/tests.md`](tests.md) and inspect both the production call chain and the tests patching `stop_worker` or `callback`. Delete `_sync_runtime_hooks()`, `_DEFAULT_*` identity checks, the `strict_mode` dual-track flag, and the `except Exception` mock wrapper; update tests to patch the symbol at its real lookup site or pass explicit dependencies, and configure test doubles to return the full `(result, blocked)` tuple contract.

```python
def execute_step(step: Step, callback, **kwargs) -> tuple[StepResult, set[str]]:
    result, blocked = callback(step, **kwargs)
    return result.finalize(), blocked
```

**Counterweight (When to Keep):** Documented user-facing plugin hooks or dependency-injection registries where production callers register custom callbacks through an explicit configuration API are genuine extension points.

## 9. Throw-Catch Circles, Single-Policy Dispatchers & Bypass Subclasses

### Single-caller throw-catch circles and single-policy dispatchers

**Pattern Name & Default Grade Hint:** Single-caller throw-catch circle / ghost polymorphism — `[Hard]`, **Error Washing** / **Half-Moves & Zombie Scaffolding** (or `[Simplify]` for pure pass-through shims and re-export shells).

**Signal (Bad):** A helper with a single caller raises an exception when an optional object is `None`, and its sole caller immediately catches `(DomainError, ValueError, TypeError)` to turn it back into `None`; or a codebase with only one live policy/table/schema version still packs `"policy": "portfolio"` into a dict to branch on `cfg.get("policy") == "portfolio"`, threads `source_table` / `(version, source_table)` across layers, or leaves single-line alias wrappers and pure re-export modules after a move.

```python
def build_calendar(tables: dict) -> Calendar:
    if tables.get("calendar") is None:
        raise ValidationError("Calendar table is missing")
    return Calendar(tables["calendar"])


class Validator:
    def __init__(self, tables: dict):
        try:
            self.calendar = build_calendar(tables)
        except (ValidationError, ValueError, TypeError):
            self.calendar = None
```

**1-Hop Verify & Fix (Good):** Check all callers of `build_calendar`, the policy dispatcher, or the wrapper module. When `Validator.__init__` is the sole caller and treats missing calendar data as `Optional[Calendar]`, return `None` directly; when only one policy, table, or module location remains, call it directly and delete the re-export shell or dead dispatch branches.

```python
def build_calendar(tables: dict) -> Calendar | None:
    raw = tables.get("calendar")
    return Calendar(raw) if raw is not None else None


class Validator:
    def __init__(self, tables: dict):
        self.calendar = build_calendar(tables)
```

**Counterweight (When to Keep):** Keep exception-raising helpers when multiple callers require strict failure while one boundary caller explicitly converts a narrow domain exception into a user-facing diagnostic, and keep re-export modules when they are part of a documented external package `__all__` contract.

### Read-side `.strip()` identity collapse and single-method bypass subclasses

**Pattern Name & Default Grade Hint:** Read-side identity `.strip()` & bypass subclass — `[Hard]`, **Split Paths & Compensation** (**Semantic Fidelity** breach).

**Signal (Bad):** A read or lookup path calls `.strip()` on persisted entity keys—collapsing distinct stored records `" B1 "` and `"B1"` into the same identity and hijacking sibling records—and a caller works around the bug by defining a single-method subclass solely to override the method without `.strip()`.

```python
class BaseQueryService:
    def resolve_view(self, account_id: str) -> View:
        normalized = str(account_id or "").strip()
        return self.repo.get_by_key(normalized)


class ExactQueryService(BaseQueryService):
    """Preserve exact stored account keys without stripping whitespace."""

    def resolve_view(self, account_id: str) -> View:
        return self.repo.get_by_key(account_id)
```

**1-Hop Verify & Fix (Good):** Inspect `BaseQueryService.resolve_view` and its callers. Enforce whitespace rules once at the write/creation admission boundary, preserve exact stored strings on read and reference lookup paths, fix `BaseQueryService.resolve_view` at its source, and delete the compensating `ExactQueryService` subclass.

```python
class BaseQueryService:
    def resolve_view(self, account_id: str) -> View:
        return self.repo.get_by_key(account_id)
```

**Counterweight (When to Keep):** Trimming user-typed search queries or normalizing new identifiers at the write/import admission boundary (while rejecting inputs where stripping collides with an existing distinct key) is valid boundary parsing.

## 10. Round-Trip Conversions, Copy Churn & Cache-Busting Copies

### Serialization, datetime, and `int(float(x))` round-trip conversions

**Pattern Name & Default Grade Hint:** Precision-losing or redundant round-trip conversion — `[Hard]`, **Error Washing** / **Redundant Churn** when `int(float(x))` loses 64-bit integer precision (`> 2^53`); `[Simplify]`, **Redundant Churn**, for `json.loads(json.dumps(x))`, `fromisoformat(dt.isoformat())`, and `set(list(set(x)))`.

**Signal (Bad):** Code uses `json.loads(json.dumps(x))` as an internal deep copy or self-check, converts a parsed `datetime` to `.isoformat()` only for the next layer to call `datetime.fromisoformat(...)`, wraps `set(list(set(items)))`, or routes integer validation through `int(float(value))`—which silently rounds 64-bit integers `> 2^53` (`9007199254740993` becomes `9007199254740992`).

```python
def parse_entity_id(value: int | str) -> int:
    return int(float(value))


def build_event(payload: dict, started_at: datetime) -> tuple[dict, datetime]:
    copied = json.loads(json.dumps(payload))
    return copied, datetime.fromisoformat(started_at.isoformat())
```

**1-Hop Verify & Fix (Good):** Check the producer and consumer types and whether any 1-hop consumer mutates `payload` in place. Parse external strings once at entry, pass strong types (`int`, `datetime`, `dict`, `set`) internally, parse integers directly via `int(value)` (after excluding `bool`) or `Decimal` to preserve full 64-bit precision, and pass `payload` directly when consumers only read it.

```python
def parse_entity_id(value: int | str) -> int:
    if isinstance(value, bool):
        raise ValueError("Boolean is not a valid integer ID")
    return int(value)


def build_event(payload: dict, started_at: datetime) -> tuple[dict, datetime]:
    return payload, started_at
```

**Counterweight (When to Keep):** When a downstream consumer genuinely mutates a nested structure and requires detached copy isolation, preserve isolation with `copy.deepcopy(payload)` or a targeted shallow copy. Keep explicit wire-contract round-trip assertions in serialization tests, and use `Decimal` when an external numeric string contract explicitly permits scientific notation while requiring exact integer conversion.

### Redundant `[dict(r)]`, `dataclasses.asdict()`, and defensive container copies

**Pattern Name & Default Grade Hint:** Unaliased copy churn — `[Simplify]`, **Redundant Churn**.

**Signal (Bad):** Code wraps `[dict(row) for row in self.fetchall()]` when `fetchall()` already materializes fresh dicts, calls `dataclasses.asdict(item)` or `deepcopy(item)` inside a loop over immutable scalar dataclasses, or copies a list at both caller and callee when neither mutates it in place.

```python
def summarize_results(repo, results: list[OutcomeRecord]) -> list[dict]:
    rows = [dict(row) for row in repo.fetchall("SELECT id, status FROM jobs")]
    serialized = [dataclasses.asdict(outcome) for outcome in results]
    return rows + serialized
```

**1-Hop Verify & Fix (Good):** Inspect `repo.fetchall` and the loop body consuming `results`. When `fetchall()` already returns new `dict` instances and `OutcomeRecord` is read without mutation, consume `repo.fetchall(...)` and dataclass attributes directly; if a downstream step needs a remapped dict, construct the new dict once at that transformation step.

```python
def summarize_results(repo, results: list[OutcomeRecord]) -> list[dict]:
    rows = repo.fetchall("SELECT id, status FROM jobs")
    serialized = [{"id": r.id, "score": r.score} for r in results]
    return rows + serialized
```

**Counterweight (When to Keep):** Keep an explicit copy when the caller retains and mutates the collection across iterations, when converting `sqlite3.Row` objects at the single repository boundary that owns dict materialization, or when a documented public API contract promises a detached mutable copy.

### Hot-loop `copy(obj)` busting `id(obj)` caches and unique row IDs in shared cache keys

**Pattern Name & Default Grade Hint:** Cache-busting copy & zero-hit cache key — `[Simplify]`, **Redundant Churn** (`[Hard]` when 0% cache hits cause algorithmic blowup at supported load).

**Signal (Bad):** A hot loop calls `copy(base_item)` on every iteration to override 1–2 fields while downstream caches key on `id(item)` (busting every `id()` cache), or a shared window/group cache includes a per-row unique identifier (`entry_id`) in its cache key `(entry_id, range_start, range_end)`, guaranteeing a 0% cross-row hit rate.

```python
def evaluate_batch(items: list[Entry], cache: dict) -> list[int]:
    scores = []
    for item in items:
        candidate = copy.copy(item)
        bucket_key = (candidate.entry_id, candidate.range_start, candidate.range_end)
        if bucket_key not in cache:
            cache[bucket_key] = compute_quota(
                candidate.team_id, candidate.range_start, candidate.range_end
            )
        scores.append(score_with_id_cache(candidate, cache[bucket_key]))
    return scores
```

**1-Hop Verify & Fix (Good):** Inspect the downstream cache keys and the fields consumed by `compute_quota` and `score_with_id_cache`. Key shared caches strictly on the dimensions that determine the result (`(team_id, range_start, range_end)`), and either pass override fields explicitly or intern variant instances so `id(obj)` caches remain stable across iterations.

```python
def evaluate_batch(items: list[Entry], cache: dict) -> list[int]:
    scores = []
    for item in items:
        bucket_key = (item.team_id, item.range_start, item.range_end)
        if bucket_key not in cache:
            cache[bucket_key] = compute_quota(
                item.team_id, item.range_start, item.range_end
            )
        scores.append(score_with_id_cache(item, cache[bucket_key]))
    return scores
```

**Counterweight (When to Keep):** When the computation genuinely depends on per-item identity (such as a memoized per-item recursive traversal within a DAG where the same `entry_id` is visited multiple times across paths), `entry_id` belongs in the cache key.
