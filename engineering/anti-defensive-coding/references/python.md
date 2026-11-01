# Python Catalogue

## 1. Reflection on Owned Types

### Owned attribute hedging

**Pattern Name & Grade Condition:** Owned attribute hedging — `[Simplify]`, **Revalidation**, when the receiver's construction or immediate producer guarantees the attribute; `[Hard]` when the fallback masks a broken mock or a test-only state that diverges from production (see `tests.md` §5).

**Signal (Bad):** A receiver whose governing type guarantees the attribute, including a third-party class, is treated as a partially initialized dynamic object.

```python
def read_total(reader):
    if not hasattr(reader, "totals"):
        reader.totals = {}
    return getattr(reader, "count", 0)
```

**1-Hop Verify & Fix (Good):** Inspect the receiver's governing type and immediate producer, then its construction and lifecycle writes: are `totals` and `count` guaranteed here? For owned state, inventory immediate callers and tests, including `object.__new__` and spec'd mocks that bypass construction. If only an incomplete fixture relies on absence, repair the fixture and remove the accommodation (`tests.md` §5). Initialize ordinary owned fields at construction and read them directly. A guaranteed attribute with a nullable *value* still supports direct access and a value check; a receiver union or an optional capability may require a presence check.

```python
class Reader:
    def __init__(self):
        self.totals = {}
        self.count = 0


def read_total(reader: Reader):
    return reader.count
```

**Counterweight (When to Keep):** A dynamic proxy, plugin, or versioned third-party API may genuinely omit an optional capability; external ownership alone does not establish that exception. A same-file attribute with the same name proves nothing about the receiver. For an owned object, absence can deliberately mean unread, unopened, uncomputed, or not yet established by a setter (for example, `ResponseNotRead` or an unset elapsed time). Check every same-class writer and constructor path before replacing that state model. Preserve presence checks when `""` or `b""` are valid materialized values: truthiness would confuse an empty result with an unread one.

### Required model fields disguised as optional or rechecked at runtime

**Pattern Name & Grade Condition:** Required-field reflection and type revalidation — `[Simplify]`, **Revalidation**, when the admission boundary or owned writers already guarantee the field, its type, or a field correlation.

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

**Correlated owned-field variant:** A constructor establishes `host_regex = None` exactly when `host` is empty, and all later writers preserve that relation. The first conjunct below repeats the second (as in httpx's `URLPattern.matches`):

```python
# Bad: rechecks the same owned invariant twice.
if self.host and self.host_regex is not None and not self.host_regex.match(other.host):
    return False

# Good: use the field that controls the operation.
if self.host_regex is not None and not self.host_regex.match(other.host):
    return False
```

Inspect the constructor and setters at 1 hop; keep both guards when supported writers can vary the fields independently.

**Counterweight (When to Keep):** Sparse external payloads, public partial-update models, `Union` / `Optional` / `Any` inputs, and versioned plugin records can legitimately omit fields or vary by runtime type. Verify the upstream admission guarantee before tightening annotations or removing runtime type checks.

### Required protocol methods probed for incomplete stubs

**Pattern Name & Grade Condition:** Test-stub capability fallback — `[Hard]`, **Half-Moves & Zombie Scaffolding**, when the fallback masks an incomplete test double; `[Simplify]`, **Dead Plumbing**, when every production implementation provides the method and no test reaches the fallback.

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

**Pattern Name & Grade Condition:** Fixed-shape dict `.get()` hedging — `[Simplify]`, **Revalidation**, when the producer always writes the key; `[Hard]`, **Error Washing**, when the producer can omit the key and the fallback silently substitutes stale state or hides a missing SQL projection column.

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

**Pattern Name & Grade Condition:** Unnarrowed variant-dict `.get()` — `[Hard]`, **Error Washing**, when another variant can reach the read and its missing key silently yields a wrong result; `[Simplify]`, **Revalidation**, when narrowing on the discriminant is already established.

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

**Pattern Name & Grade Condition:** Static literal self-check — `[Simplify]`, **Revalidation**, when the validated table is a module-owned literal, or **Dead Plumbing** when the validated config section has no producer.

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

**Pattern Name & Grade Condition:** Repeated static-table linear scan — `[Simplify]`, **Redundant Churn**, when the table is fixed at import time.

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

**Pattern Name & Grade Condition:** Internal exception washing / **Moved Defense** — `[Hard]`, **Error Washing**, when a reachable internal defect or corrupt input becomes false success, omitted required work, or fail-open behavior at the caller; `[Simplify]`, **Revalidation**, when the immediate producer or governing type makes the caught failure unreachable. An established recovery contract is a **Counterweight**. Apply `SKILL.md` Step 4 to these three outcomes; add **Half-Moves & Zombie Scaffolding** when the catch was relocated into callees during a refactor.

**Signal (Bad):** Internal typed computation is wrapped in `except Exception:` or a multi-exception tuple (`KeyError`, `TypeError`, `AttributeError`, `ValueError`, `OverflowError`) that returns a fallback value or gets copy-pasted/migrated into downstream constructors and helpers when an upstream catch is removed. The exception set is a review trigger: `TypeError` and `ValueError` also occur in legitimate boundary parsers.

```python
def compute_allocation(plan: ValidatedPlan) -> dict[str, int]:
    try:
        return {item.id: item.units * plan.rate for item in plan.items}
    except (DomainError, KeyError, TypeError, ValueError, OverflowError):
        return {}
```

**1-Hop Verify & Fix (Good):** Inspect `ValidatedPlan` and the immediate callers (including any surviving caller that lost the catch in the same diff). Which failure outcome reaches the caller or operator: false success, omitted required work, or fail-open behavior? Identify the producer's actual failure contract and the consumer's response before choosing recovery. Remove catches around established internal invariants; confine boundary recovery to the operation and failure it can handle. A handler ending in a same-file `NoReturn` helper still needs review: the immediate producer may already translate that failure, leaving a duplicate translation layer.

```python
def compute_allocation(plan: ValidatedPlan) -> dict[str, int]:
    return {item.id: item.units * plan.rate for item in plan.items}
```

**Exact-handler lint ownership:** Apply the **Lint-Owned** test in `SKILL.md` Step 3 to the actual handler header (`BLE001`) or handler statement (`S110`, `TRY400`); a diagnostic inside a nested handler does not own the outer one. Name the remedy: narrow a best-effort Discord acknowledgement to `discord.HTTPException` and retain the appropriate traceback, for example. Check the outcome after that remedy. An imported framework call may mutate a local registry rather than perform external I/O.

| Narrowing leaves an established recovery outcome | Narrowing leaves Error Washing |
| --- | --- |
| A secondary lease-renewal POST can catch `httpx.RequestError` while the primary heartbeat remains authoritative. | Catching `JSONDecodeError` still conflates malformed nonempty request JSON with an absent body when both become `{}`. |
| A preparatory unarchive can catch HTTP failures when the delivery caller separately records delivery failure and cleans partial evidence. | Catching `OSError` around a failed backup still falsely claims “backed up” before returning empty state. |
| A failed refusal edit can fall back to a send while remaining fail-closed; a failed `defer()` can return before mutation. | Catching documented command-registration errors still hides an omitted local registry entry. |
| Expected transport failures can be reported without claiming that the operation succeeded. | Catching HTTP/NotFound still hides an ephemeral message edited through the wrong, guaranteed-to-fail channel route. |

Duplicate-column and real migration failures both use `sqlite3.OperationalError`; apply the schema-inspection remedy in `sql.md` §8 instead of narrowing alone.

**Multi-exception pair:** Heterogeneous wire status may legitimately become an explicit “unrecognized” sentinel; malformed supplied configuration must not become a valid priority/channel `0` or retention `30`. An “unknown” display label preserves uncertainty rather than fabricating valid state.

```python
def parse_wire_status(raw):
    try:
        return Status(int(raw))
    except (TypeError, ValueError):
        return UNRECOGNIZED  # The wire/display contract exposes this outcome.


def configured_channel(raw):
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0  # Bad: malformed supplied config silently disables reporting.
```

Keep the documented absent-configuration default separate; reject or report a malformed supplied value instead of inventing a valid one.

**Counterweight (When to Keep):** Recovery must preserve the failure outcome or the data the immediate consumer relies on. Logging alone does not establish recovery.

- Boundary parsing of untrusted wire/file input with exact exception types and an explicit invalid/unknown result, or a consumed failed future/structured result, preserves failure as failure. Fail-closed rejection and best-effort shutdown cleanup can likewise satisfy their contracts.
- Worker/request/scheduler/listener isolation records full tracebacks for unexpected failures and exposes the failed outcome. A top catch in a coroutine already detached by its caller (`asyncio.create_task` with no result consumer) adds no isolation; keeping it requires full-traceback logging or compensation for partial side effects, and the outcome test above still applies.
- Per-item best-effort delivery to an external service returns an outcome the caller acts on: for example, the caller raises when neither voice nor text landed, or records each failed delivery. A blanket `continue` that silently omits required work does not supply that outcome.
- Fail-safe merge/I/O recovery reports failure (such as `None`) and the immediate caller keeps the original branch/data instead of deleting it. Check that the failed operation is not later reported as successful.
- Documented in-process transport/test-client modes may surface the application's failure as an HTTP 500 response behind a public flag that defaults to re-raise, such as ASGI `raise_app_exceptions`.

### `json.loads(..., object_pairs_hook=...)` washed under `except ValueError`

**Pattern Name & Grade Condition:** Hook rejection diagnostic erasure — `[Hard]`, **Error Washing**, when the hook's specific rejection is masked behind the generic parse error or fallback.

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

### Moved and copied defenses across a refactor

**Pattern Name & Grade Condition:** **Moved Defense** / copied defense — grade a moved defense by the condition of its own entry (§1, §4 or §6), adding **Half-Moves & Zombie Scaffolding** when a hedge was relocated unchanged into a nested helper or extracted function; `[Simplify]`, **Revalidation**, for a copied guard whose owner performs the same check on the same argument.

**Signal (Bad):** A refactor deletes a reflection probe, broad catch, or falsy fallback from a surviving function and adds one with the same signature to another function (`PY10a_moved_defense`), or a callee takes ownership of a guard while its callers keep wrapping the call in that guard. A copied guard loses nothing, so `PY10a` stays silent on it.

```python
async def close_async_iterator(iterator):
    if hasattr(iterator, "aclose"):
        await iterator.aclose()


async def finish(stream):
    if hasattr(stream, "aclose"):  # Repeats the guard the helper now owns.
        await close_async_iterator(stream)
```

**1-Hop Verify & Fix (Good):** A `PY10a` candidate is a signature match only: the same rule with the same caught exception names or attribute/key literals, receiver dropped. First open each origin it names at the base and confirm it guarded the same producer, receiver, or exception source as the destination; otherwise dismiss the pair as a signature coincidence. Once the origin matches, ask whether the callee became the owner of the rule, then check the owner's callers for a copied guard and call the owner directly.

```python
async def finish(stream):
    await close_async_iterator(stream)
```

**Counterweight (When to Keep):** A genuine ownership transfer, where the callee now owns the rule for every caller, leaves only the defense itself to judge under its own entry. A caller keeps its guard when the guard also selects a different branch, such as an `else` that releases a synchronous source.

## 5. Premature Defaults & Corrupt-State `None` Sentinels

### Premature default shadowing a required-value guard

**Pattern Name & Grade Condition:** Default-shadowed validation — `[Hard]`, **Error Washing**, when the producer can omit the required value and the default lets it pass the check as valid input; `[Simplify]`, **Dead Plumbing**, when the producer always supplies it, leaving both default and check dead.

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

**Pattern Name & Grade Condition:** Corrupt-state `None` conflation — `[Hard]`, **Error Washing**, when a caller can pass corrupt stored `None` and it silently triggers recomputation instead of rejection.

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

**Pattern Name & Grade Condition:** Falsy coalescing — the immediate producer or governing type selects one of three outcomes. **Dead fallback**: `[Simplify]`, **Dead Plumbing**, when no falsy value can arrive; a fallback that is harmless because it never fires is dead plumbing. **Lossy fallback**: `[Hard]`, **Error Washing**, when the producer can deliver a legitimate falsy value distinct from the fallback, which the fallback rewrites (broken **Semantic Fidelity**). **Boundary normalization**: a **Counterweight** when the producer's contract equates missing with empty, or the producer already rejects empty so that `or` behaves exactly like `is None`.

**Signal (Bad):** An `or` fallback (`cfg.get("timeout") or 30`, `metrics.get("pending_count") or len(scan_all())`) overwrites legitimate `0`, `0.0`, `""`, `[]`, or `False` domain values with a non-zero default or forces a full cache-miss recomputation whenever the cached result is `0` or `[]`.

```python
def resolve_pending_summary(cfg: dict, metrics: dict, all_items: list) -> tuple[int, int]:
    timeout = cfg.get("timeout") or 30
    pending = metrics.get("pending_count") or len([x for x in all_items if not x.done])
    return timeout, pending
```

**1-Hop Verify & Fix (Good):** Check the domain of the left-hand expression at its immediate producer or governing type. When `0`, `""`, `[]`, or `False` is a valid value or cached result, test `is not None` (or `key in mapping`) explicitly so **Semantic Fidelity** is preserved. When no falsy value can arrive, consume the value directly and delete the fallback.

```python
def resolve_pending_summary(cfg: dict, metrics: dict, all_items: list) -> tuple[int, int]:
    raw_timeout = cfg.get("timeout")
    timeout = raw_timeout if raw_timeout is not None else 30
    cached = metrics.get("pending_count")
    pending = cached if cached is not None else len([x for x in all_items if not x.done])
    return timeout, pending
```

**Recognition shapes:**

- **Whole expression.** Compare the complete expression, including an enclosing coercion and every operand of an alias chain (`a.get("new") or a.get("old") or ""`). `str(x or "")` erases a numeric `0` or `False` although the fallback is empty. `int(x or 0)` still raises on non-empty invalid text but turns falsy invalid input such as `""` into `0`; call it dead only after establishing the actual producer domain. A fallback can also be an identity operation on an already-normalized string or integer.
- **Dictionary receiver provenance.** For a dictionary lookup, record the receiver's immediate producer, caller argument, or governing schema, and classify it as a raw boundary map, a producer-normalized map, or an open internal map. Provenance comes from that binding: `dict[str, Any]` establishes neither required keys nor external provenance, and neither does a receiver name such as `args`, `entry`, or `data`. Raw JSON or configuration is a boundary clue, not proof that every falsy value means missing. Conversely, a concrete dictionary constructor or a normalized row adapter can establish a guarantee despite an `Any`-valued annotation.
- **Dead missing-key default.** `mapping.get(k, d)` on a mapping whose immediate producer always writes `k` never applies `d`: dead plumbing. Rows from a `SELECT` follow `sql.md` §2.
- **Truthiness that selects an operation.** `method = "POST" if content or data or files or json else "GET"` behind a parsed-JSON producer sends `json={}`, `[]`, `0`, `false`, or `""` with `GET`: a legitimate falsy value selects the wrong operation, which is the lossy outcome. Test presence with `is not None`.
- **`IfExp` default.** `x if x else d` follows the same three outcomes. `stats["join_count"] if stats else 1` after `INSERT OR IGNORE` and a re-read on the same connection never takes the `else` branch: dead plumbing.

**Counterweight (When to Keep):** The equivalence or rejection belongs to the producer's or governing type's contract; cite that declaration.

- Blank optional form text that means "unset" at a normalization boundary (`raw.strip() or None`).
- **Governing-type Optional asymmetry.** When the destination type records presence only in Optional sibling fields and its producer documents that split, absent ≡ empty is the schema contract for a non-Optional field, and a fidelity concern belongs to the schema rather than this coalesce. httpx `ParseResult` declares `userinfo: str` and `host: str` but `query: str | None` and `fragment: str | None`, and the parser documents that `None` marks a missing `?` or `#` while the other components may be empty, so `userinfo or ""` and `host or ""` follow the schema. Without that documented split, a required non-Optional field may call for rejecting the missing value instead.
- **`None`-or-identifier domains.** A codec name or MIME type is either `None` or non-empty (`self._encoding or "utf-8"`, `mimetypes.guess_type(name)[0] or "application/octet-stream"`), so the fallback overwrites no legitimate falsy value.
- **Presence-keeping accessors.** A delimiter-free public accessor may return `""` for an absent component while the stored form keeps presence (`URL.query` returns bytes without `?`, while `raw_path` keeps `b"/p?"`).
- **HTTP empty path.** An empty request path is sent as `/` in origin form (`path or "/"`).
- **CLI `None` defaults.** A string-valued argparse option declared without a default has the domain `None | str`, so `""` is its only falsy value. When the parser or `main()` rejects `""` (`args.tip or "HEAD"` after `main()` rejects `--tip ""`), or `""` equals the fallback (`Path(args.repo or ".")`, since `Path("") == Path(".")`), `or` is exact. A typed option such as `type=int` also delivers `0`; check the parser declaration for every reachable falsy value.

### Pre-validation `==` diffing (`True == 1`) and lax integer guards

**Pattern Name & Grade Condition:** Pre-validation boolean/numeric conflation — `[Hard]`, **Error Washing**, when unvalidated input can carry a `bool`, Unicode digit, fractional value, or missing `NOT NULL` value that the diff or parser accepts as valid (broken **Semantic Fidelity**).

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

**Pattern Name & Grade Condition:** Linter-evading dead parameter — `[Simplify]`, **Dead Plumbing**, when no caller relies on the discarded or overwritten parameter.

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

**Pattern Name & Grade Condition:** Pseudo-optional context fallback chain — `[Simplify]`, **Dead Plumbing**, when every production entry point passes a context; the repository methods only the fallback reaches belong to the same finding.

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

**Pattern Name & Grade Condition:** Hidden state or dependency smuggling — `[Simplify]`, **Hidden Flow**; `[Hard]` only when the smuggled state causes a listed consequence, such as transient `self` state shared across concurrent or reentrant calls.

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

**Pattern Name & Grade Condition:** Runtime test-hook trampoline & mock accommodation — `[Hard]`, **Half-Moves & Zombie Scaffolding**, when the hook sync or `_DEFAULT_*` check masks tests that patch the wrong lookup site, or a dropped mode flag silently runs the lenient path for a caller that requested strict mode; **Error Washing** when the catch masks a broken mock's unpacking error.

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

**Pattern Name & Grade Condition:** Single-caller throw-catch circle / ghost polymorphism — `[Hard]`, **Error Washing**, when the caller's catch can mask defects beyond the helper's own raise (such as a `TypeError` from construction); `[Simplify]`, **Half-Moves & Zombie Scaffolding** or **Dead Plumbing**, for an exact throw-catch circle, a single-policy dispatcher's dead branches, a pure pass-through shim, or a re-export shell.

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

**Pattern Name & Grade Condition:** Read-side identity `.strip()` & bypass subclass — `[Hard]`, **Split Paths & Compensation**, when stored keys can differ only by whitespace, so the read path collapses distinct records (broken **Semantic Fidelity**); `[Simplify]`, **Revalidation**, when admission already guarantees stripped keys.

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

### Serialization, datetime, and `int(float(x))` round-trips; same-type scalar reparsing

**Pattern Name & Grade Condition:** Precision-losing or redundant round-trip conversion, or same-type scalar reparsing — `[Hard]`, **Error Washing**, when `int(float(x))` can receive integers above `2^53` or fractional values and silently rounds or truncates them (broken **Semantic Fidelity**); `[Simplify]`, **Redundant Churn**, for `json.loads(json.dumps(x))`, `fromisoformat(dt.isoformat())`, `set(list(set(x)))`, and an `int(float(x))` whose producer delivers only integers within `2^53`; `[Simplify]`, **Revalidation**, for a same-type reparse whose immediate callers all construct that exact type.

**Signal (Bad):** Code uses `json.loads(json.dumps(x))` as an internal deep copy or self-check, converts a parsed `datetime` to `.isoformat()` only for the next layer to call `datetime.fromisoformat(...)`, wraps `set(list(set(items)))`, routes integer validation through `int(float(value))`—which silently rounds 64-bit integers `> 2^53` (`9007199254740993` becomes `9007199254740992`)—or reparses a parameter into the scalar type its annotation already declares (`int(base_count or 0)` on `base_count: int`, `str(session_id or "")` on `session_id: str`).

```python
def parse_entity_id(value: int | str) -> int:
    return int(float(value))


def build_event(payload: dict, started_at: datetime) -> tuple[dict, datetime]:
    copied = json.loads(json.dumps(payload))
    return copied, datetime.fromisoformat(started_at.isoformat())


def next_sequence(base_count: int) -> int:
    return int(base_count or 0) + 1
```

**1-Hop Verify & Fix (Good):** Check the producer and consumer types and, for a copying round-trip, whether another owner retains `payload` while one side mutates it (**Freshness before mutation** in the copy-churn entry below). Parse external strings once at entry, pass strong types (`int`, `datetime`, `dict`, `set`) internally, parse integers directly via `int(value)` (after excluding `bool`) or `Decimal` to preserve full 64-bit precision, and pass `payload` directly when it is fresh or only read. For a same-type reparse, inspect what each immediate caller actually constructs (a caller that only forwards its own annotated value proves nothing), and use the value directly once every caller builds that exact type.

```python
def parse_entity_id(value: int | str) -> int:
    if isinstance(value, bool):
        raise ValueError("Boolean is not a valid integer ID")
    return int(value)


def build_event(payload: dict, started_at: datetime) -> tuple[dict, datetime]:
    return payload, started_at


def next_sequence(base_count: int) -> int:
    return base_count + 1
```

**Counterweight (When to Keep):** When another owner retains `payload` and one side mutates a nested structure, preserve isolation with `copy.deepcopy(payload)` or a targeted shallow copy. Keep explicit wire-contract round-trip assertions in serialization tests, and use `Decimal` when an external numeric string contract explicitly permits scientific notation while requiring exact integer conversion. Keep a same-type reparse at public, SDK, route, or plugin entry points, on values read from `Any` configuration, and where it deliberately converts a `str` subclass or enum to its base type or crosses the numeric tower (`bool` to `int`, `int` to `float`).

### Redundant `[dict(r)]`, `dataclasses.asdict()`, and defensive container copies

**Pattern Name & Grade Condition:** Unaliased copy churn — `[Simplify]`, **Redundant Churn**, when the source is fresh (retained by no other owner) or neither side mutates after the copy.

**Signal (Bad):** Code wraps `[dict(row) for row in self.fetchall()]` when `fetchall()` already materializes fresh dicts, calls `dataclasses.asdict(item)` or `deepcopy(item)` inside a loop over immutable scalar dataclasses, or copies a list at both caller and callee when neither mutates it in place.

```python
def summarize_results(repo, results: list[OutcomeRecord]) -> list[dict]:
    rows = [dict(row) for row in repo.fetchall("SELECT id, status FROM jobs")]
    serialized = [dataclasses.asdict(outcome) for outcome in results]
    return rows + serialized
```

**1-Hop Verify & Fix (Good):** Inspect `repo.fetchall`, the loop body consuming `results`, and any other owner that retains a copied source. When `fetchall()` already returns new `dict` instances and `OutcomeRecord` is read without mutation, consume `repo.fetchall(...)` and dataclass attributes directly; if a downstream step needs a remapped dict, construct the new dict once at that transformation step.

```python
def summarize_results(repo, results: list[OutcomeRecord]) -> list[dict]:
    rows = repo.fetchall("SELECT id, status FROM jobs")
    serialized = [{"id": r.id, "score": r.score} for r in results]
    return rows + serialized
```

**Recognition shapes:**

- **Freshness before mutation.** A value produced for this caller and retained by no other owner is fresh: rows from a dict-building row factory, a `model_dump()` or `json.loads(...)` result, a subtree of an earlier `deepcopy`. A consumer may mutate a fresh value in place without copying it.
- **Row factory.** For `dict(row)`, the row factory of the cursor that produced the row separates churn from the first materialization; `sql.md` §3 holds the cursor rule.
- **Deep copy of an already-detached value.** After `source = copy.deepcopy(payload)`, `normalize(copy.deepcopy(source["request"]))` copies a subtree of the fresh `source`; when nothing reads the un-normalized subtree afterwards, normalize `source["request"]` directly.
- **Deep copy fed straight to `json.dumps`.** `snapshot = {"tools": copy.deepcopy(tools)}` serialized by `json.dumps(snapshot)`, with no mutation or `await` in between and `snapshot` not kept afterwards, copies a value that `json.dumps` only reads; put `tools` in the snapshot directly.

**Counterweight (When to Keep):** Keep an explicit copy when another owner retains the source and one side mutates: entries copied out of a lock-guarded registry and mutated after release, a merged value cached for later calls and handed out per call, source and target trees kept side by side. Also keep it at the first materialization of driver rows (`sql.md` §3), or when a documented public API contract promises a detached mutable copy.

### Hot-loop `copy(obj)` busting `id(obj)` caches and unique row IDs in shared cache keys

**Pattern Name & Grade Condition:** Cache-busting copy & zero-hit cache key — `[Simplify]`, **Redundant Churn**; `[Hard]` when the 0% hit rate causes algorithmic blowup at supported load.

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

## 11. In-Process Lock Critical Section Discipline

### File I/O, network sends, or hashing inside `threading` or `asyncio` locks

**Pattern Name & Grade Condition:** In-process lock I/O, network send, or hashing bleed — `[Hard]`, **Redundant Churn**, when file I/O, network sends, or heavy hashing inside a `threading` or `asyncio` lock causes lock or concurrency-isolation safety failures or contention timeouts at supported load; `[Simplify]`, **Redundant Churn**, for movable work inside the lock that causes none of those failures.

**Signal (Bad):** While holding an in-process `threading.Lock` / `threading.RLock` or `asyncio.Lock` (including per-key lock maps such as `async with self.user_locks[user_id]:`), code sends awaited network replies (`await interaction.followup.send(...)`) on early rejection or after state updates, reads static files from disk, writes a snapshot whose inputs are already captured in local scalars, or runs heavy hashing (`hashlib.sha256(...)`) that does not depend on lock-protected state.

```python
async def submit_answer(self, interaction, user_id: int, choice: str) -> None:
    async with self.user_locks[user_id]:
        session = self.sessions.get(user_id)
        if session is None or session.expired:
            await interaction.followup.send("Session has ended.", ephemeral=True)
            return
        if session.answered:
            await interaction.followup.send("Already answered.", ephemeral=True)
            return
        session.record(choice)
        await interaction.followup.send("Answer recorded.", ephemeral=True)
```

**1-Hop Verify & Fix (Good):** Inspect the lock's critical section and every same-owner reader/writer of the state it guards (`self.sessions` or the shared resource). Keep only the state read, decision, and mutation (or scalar snapshot capture) inside the `with` / `async with` block; move awaited network replies (`await interaction.followup.send(...)`), static file reads, independent snapshot writes, and pure CPU hashing before lock acquisition or after lock release. For database write transactions (`BEGIN IMMEDIATE`), see `sql.md` §7.

```python
async def submit_answer(self, interaction, user_id: int, choice: str) -> None:
    async with self.user_locks[user_id]:
        session = self.sessions.get(user_id)
        if session is None or session.expired:
            reply = "Session has ended."
        elif session.answered:
            reply = "Already answered."
        else:
            session.record(choice)
            reply = "Answer recorded."
    await interaction.followup.send(reply, ephemeral=True)
```

**Counterweight (When to Keep):** Keep the operation inside the lock when removing it breaks serialization or state consistency: (1) writer-serialized temp-file write plus `os.replace` publication, especially when writers share a fixed `.tmp` path that concurrent writers would overwrite or unlink before replacement; (2) append-log ordering where in-memory sequence allocation, disk append, and in-memory log append share one critical section so file order matches sequence order; (3) a file or stream sink that the lock exists specifically to protect (with payload serialization already done outside the lock); or (4) persisting state and resetting its `_dirty` flag atomically under the same lock so concurrent updates between the write and the flag reset are not lost.
