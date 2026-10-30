# Tests Catalogue

## 1. Self-Justifying Corruption Tests

### Impossible required-field fixture

**Pattern Name & Default Grade Hint:** Impossible required-field fixture — `[Simplify]`, **Implementation-Coupled Tests**.

**Signal (Bad):** A test overwrites an admitted field with a value its sole producer rejects, solely to keep an internal fallback alive.

```python
def test_missing_title_fallback():
    summary = make_summary(valid_request())
    summary.title = ""
    assert render_summary(summary) == "Nothing available"
```

**1-Hop Verify & Fix (Good):** Inspect `make_summary`'s construction contract. If it guarantees a non-empty title, test observable rendering with an admitted summary and remove the impossible fixture together with the dead fallback. Exercise malformed external input at admission instead.

```python
def test_renders_admitted_summary():
    summary = make_summary(valid_request(title="Ready"))
    assert render_summary(summary) == "Ready"
```

**Counterweight (When to Keep):** **Semantic Fidelity** — preserve and test an empty title when the domain or public renderer contract admits `""` as a valid value; keep fuzzing and malformed-input tests at a real untrusted boundary where they test that boundary's rejection or recovery contract.

### Fabricated internal corruption legitimizing a catch

**Pattern Name & Default Grade Hint:** Self-justifying corruption fixture — `[Hard]`, **Implementation-Coupled Tests**, when it locks in Error Washing that hides production bugs.

**Signal (Bad):** A newly added catch is justified by mutating a validated internal record into a shape that no producer emits.

```python
def test_projection_ignores_bad_columns():
    record = admit_record(valid_payload())
    record.columns = 7
    assert project_record(record) == {}
```

**1-Hop Verify & Fix (Good):** Inspect the admitted record's type and the projection's input contract. Keep the internal calculation strict; remove the corrupt fixture and catch. If malformed columns are meaningful external input, verify rejection through the public admission seam.

```python
def test_rejects_malformed_external_columns():
    payload = valid_payload()
    payload["columns"] = 7
    with pytest.raises(InvalidRecord):
        admit_record(payload)
```

**Counterweight (When to Keep):** Disk corruption, network payloads, or fault injection at a documented recovery boundary can require malformed fixtures. Preserve tests proving recovery prevents data loss; do not assume all corrupt states are impossible merely because ordinary constructors avoid them.

### Invented metadata sustaining dead degradation

**Pattern Name & Default Grade Hint:** Zero-writer metadata fixture — `[Simplify]`, **Implementation-Coupled Tests**.

**Signal (Bad):** Tests fabricate a failure flag or textual status in a current-process result whose producer only emits success or raises.

```python
def test_partial_failure_warning():
    result = build_result(valid_input())
    result.metadata["loaded"] = "no"
    result.metadata["partial_failures"] = 3
    assert summarize(result).warnings == ["Partial result"]
```

**1-Hop Verify & Fix (Good):** Read the immediate result producer. If it always completes strictly and never writes the failure metadata, remove the fabricated fixture, its dead parser/warning branch, and unconsumed constant summary fields. Test the real success and failure outcomes through the public operation.

```python
def test_rejects_failed_input():
    with pytest.raises(InputFailure):
        build_result(unreadable_input())
```

**Counterweight (When to Keep):** Persisted historical results, external producers, or a supported partial-success mode may write those fields. A current local producer alone cannot invalidate a documented cross-version input contract.

### Fixture bypasses admission

**Pattern Name & Default Grade Hint:** Admission-bypassing fixture — `[Simplify]`, **Implementation-Coupled Tests**.

**Signal (Bad):** A helper feeds raw dictionaries into an internal algorithm that production receives only after admission, sustaining duplicate type/key guards.

```python
def make_test_plan(raw):
    return calculate(Plan(keys=raw))
```

**1-Hop Verify & Fix (Good):** Inspect the immediate production caller: does it construct the algorithm's input through the admission helper? Route the fixture through that same helper and assert public behavior; remove only the redundant internal guard made unnecessary by that established contract.

```python
def make_test_plan(raw):
    return calculate(prepare_plan(raw))
```

**Counterweight (When to Keep):** A documented public or exported alias may accept raw or nullable values even if nearby internal callers do not. Keep tests for that live contract. Tests of the admission helper itself should continue supplying raw malformed inputs to verify rejection.

## 2. Mutating Read-Only Returns vs. Aliasing Contracts

### Mutating read-only getter returns to justify defensive copies

**Pattern Name & Default Grade Hint:** **Corruption Test** — read-only return mutation & defensive copy — `[Simplify]`, **Implementation-Coupled Tests** (for the return-mutating test) / **Redundant Churn** (for the production `list()` copy).

**Signal (Bad):** An internal read-only accessor copies its stored sequence via `list(...)` on every call even though no production caller mutates the returned collection, and a unit test mutates the returned list in place (`.append(...)`, `.pop()`, or item assignment) and re-calls the getter solely to prove the defensive copy cannot be removed.

```python
class ParsedBatch:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def rows(self) -> list[dict]:
        return list(self._rows)


def test_rows_returns_defensive_copy():
    batch = ParsedBatch([{"row_no": 1}])
    rows = batch.rows()
    rows.append({"row_no": 99})
    assert batch.rows() == [{"row_no": 1}]
```

**1-Hop Verify & Fix (Good):** Inspect `rows()` callers and the `ParsedBatch` ownership/lifetime contract across all aliases and writers (both getter consumers and the constructor input list). When callers only read and the contract permits a live view—or guarantees no writer mutates the underlying list while references are held—return the stored sequence directly and assert its contents. When the contract requires immutable membership, convert once to a `tuple` at construction.

```python
class ParsedBatch:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def rows(self) -> list[dict]:
        return self._rows


def test_rows_exposes_admitted_entries():
    batch = ParsedBatch([{"row_no": 1}])
    assert batch.rows() == [{"row_no": 1}]
```

**Counterweight (When to Keep):** Preserve documented detached-copy or snapshot-isolation guarantees (whether on a public API, shared cache, or internal buffer where callers mutate the returned list as a working buffer or where readers must be insulated from later producer/input mutations) as well as public immutable `tuple` return guarantees and their contract tests.

## 3. Synthetic Cross-Mode Call Combinations

### Synthetic cross-mode call combinations exercising unreachable cache or fallback branches

**Pattern Name & Default Grade Hint:** Synthetic cross-mode branch test — `[Simplify]`, **Implementation-Coupled Tests** (for the synthetic cross-mode test) / **Dead Plumbing** (for the unreachable `_pinned()` branch); `[Hard]`, **Half-Moves & Zombie Scaffolding**, when hanging the method on a shared base class exposes a dead entry point on subclasses.

**Signal (Bad):** Production calls `choices()` only on fresh standalone `ChoiceReader` instances outside `detached_snapshot()`, so `self._pinned()` is always `None`; yet a test combines `with reader.detached_snapshot():` and `reader.choices()` solely to exercise the unreachable `_pinned()` branch, while defining `choices()` on `BaseSnapshotReader` leaves `FullBatchProjection` inheriting a zero-caller method.

```python
class BaseSnapshotReader:
    def choices(self) -> list[str]:
        facts = self._pinned()
        if facts is not None:
            return facts["choices"]
        return [
            row[0]
            for row in self.conn.execute(
                "SELECT code FROM shipping_options WHERE active = 1 ORDER BY code"
            )
        ]


class FullBatchProjection(BaseSnapshotReader):
    pass


class ChoiceReader(BaseSnapshotReader):
    pass


def test_choices_uses_pinned_snapshot_when_active(db_conn):
    reader = ChoiceReader(db_conn)
    with reader.detached_snapshot():
        assert reader.choices() == ["express", "standard"]
```

**1-Hop Verify & Fix (Good):** Inspect the immediate production callers of `choices()` and `detached_snapshot()`. When they establish fresh-instance standalone use on `ChoiceReader` and zero calls on `FullBatchProjection`, move `choices()` onto `ChoiceReader`, remove the unreachable `self._pinned()` branch, and test the standalone production call pattern.

```python
class ChoiceReader:
    def __init__(self, conn):
        self.conn = conn

    def choices(self) -> list[str]:
        return [
            row[0]
            for row in self.conn.execute(
                "SELECT code FROM shipping_options WHERE active = 1 ORDER BY code"
            )
        ]


def test_choices_loads_available_options(db_conn):
    reader = ChoiceReader(db_conn)
    assert reader.choices() == ["express", "standard"]
```

**Counterweight (When to Keep):** General-purpose library primitives, re-entrant query builders, or documented multi-mode services whose public contract supports calling query methods both standalone and inside an active snapshot/transaction context require tests covering both modes.

## 4. Test-Only Entry Points, Orphaned Repositories & Private Legacy Wrappers

### Zero-production-caller `.apply()` entry points and test-resurrected orphaned repositories

**Pattern Name & Default Grade Hint:** **Zombie Scaffolding** — test-only entry point or orphaned repository — `[Hard]`, **Half-Moves & Zombie Scaffolding** (or `[Simplify]`, **Dead Plumbing**, when `PY10c_cross_test_reachability` reports a live function whose default parameter receives a uniform constant or `<DEFAULT>` at all production call sites).

**Signal (Bad):** After production routes cut over to a two-phase `stage()` + `commit_staged()` protocol—or after an upper query service is deleted—a production service retains a zero-production-caller `.apply()` wrapper, or lower-level repository methods and helpers (`InvoiceRepository.list_archived_by_region`, `_coerce_entries`) survive with zero production callers, kept alive only because legacy tests still call `.apply()` or were rewritten during cleanup to call the newly orphaned methods directly.

```python
class OrderBatchUpdater:
    def stage(self, payload: dict) -> StagedBatch:
        return StagedBatch.from_payload(payload)

    def commit_staged(self, staged: StagedBatch) -> CommitReceipt:
        return self.repo.commit(staged)

    def apply(self, payload: dict) -> CommitReceipt:
        staged = self.stage(payload)
        return self.commit_staged(staged)


def handle_batch_route(updater: OrderBatchUpdater, payload: dict) -> CommitReceipt:
    staged = updater.stage(payload)
    return updater.commit_staged(staged)


def test_order_batch_apply(updater):
    receipt = updater.apply({"code": "A1"})
    assert receipt.committed is True
```

**1-Hop Verify & Fix (Good):** Use the **Candidate Extractor**'s `PY10c_cross_test_reachability` candidate to locate the entry point, then verify its immediate caller and export contract. When production callers exclusively use `stage()` + `commit_staged()` and `.apply()` (or an orphaned repository/helper method individually surfaced by `PY10c`) has zero production callers and no external export obligation, delete `.apply()` and confirmed orphaned helpers, and migrate the test to `stage()` + `commit_staged()`. When `PY10c` instead reports a live function whose default parameter has a uniform production value while tests pass other values, verify the immediate callers and inline or remove the dead parameter branch as `[Simplify]`, **Dead Plumbing**.

```python
def test_order_batch_stage_and_commit(updater):
    staged = updater.stage({"code": "A1"})
    receipt = updater.commit_staged(staged)
    assert receipt.committed is True
```

**Counterweight (When to Keep):** Public SDK convenience methods, CLI entry points, `__all__` exports, framework-registered hooks, or documented backward-compatibility methods and default parameters with live external consumers remain valid contracts even when internal callers use two-phase primitives or omit the default parameter.

### Single-user `*_support.py` wrappers and private `._legacy_*` access

**Pattern Name & Default Grade Hint:** Private-legacy test access & single-user support wrapper — `[Hard]`, **Half-Moves & Zombie Scaffolding** (for a dead `._legacy_*` production method/attribute) / **Implementation-Coupled Tests** (for the test or wrapper accessing `._legacy_*`); `[Simplify]`, **Implementation-Coupled Tests**, when a single-consumer `*_support.py` wrapper only adds indirection around a live public API.

**Signal (Bad):** A test or a single-consumer `*_test_support.py` helper module bypasses the public service API (`provider.records_by_scope([scope])`) to read or call a private compatibility attribute (`provider._legacy_records([scope])`, flagged by `PY10b_test_smell`), keeping the private `_legacy_*` path alive in production.

```python
def load_legacy_scope(provider, scope: str) -> list[dict]:
    return provider._legacy_records([scope])


def test_scope_records(provider):
    assert load_legacy_scope(provider, "north") == [{"scope": "north", "active": True}]
```

**1-Hop Verify & Fix (Good):** Use the **Candidate Extractor**'s `PY10b_test_smell` and `PY10c_cross_test_reachability` candidates, then inspect the receiver's governing class contract, `_legacy_records`' immediate callers, and the support module's importers. When `_legacy_records` has zero production callers and no external compatibility obligation, delete `_legacy_records` and the single-use wrapper module, and assert behavior through `provider.records_by_scope([scope])`. **Live public API variant (`[Simplify]`):** When a single-consumer support wrapper only forwards to a live public API, invoke that public API directly in the test and delete only the wrapper.

```python
def test_scope_records(provider):
    assert provider.records_by_scope(["north"]) == [{"scope": "north", "active": True}]
```

**Counterweight (When to Keep):** Shared fixture factories in `conftest.py` or multi-file test support modules that seed valid domain state and invoke public entry points across multiple test suites are legitimate test infrastructure.

## 5. Production Accommodations for Incomplete Mocks & Monkeypatches

### Production `getattr` capability probes and `except Exception` blocks for incomplete test doubles

**Pattern Name & Default Grade Hint:** Mock-accommodating production probe or catch — `[Hard]`, **Half-Moves & Zombie Scaffolding** (for the `getattr` capability probe and fallback counters serving only incomplete stubs) / **Error Washing** (for the `except Exception` fallback concealing callback unpacking or runtime errors).

**Signal (Bad):** Every production implementation of `calendar` defines `billable_minutes` and every production `callback` returns a 2-tuple `(result, blocked)`, yet production code probes `getattr(calendar, "billable_minutes", None)` (spawning unreachable fallback flags, counters, and error codes) or wraps `result, blocked = callback(step)` in `except Exception: return StepResult.empty(step.id), set()` solely because unit tests pass an incomplete stub or bare `MagicMock()` whose return value fails tuple unpacking with `ValueError`.

```python
def run_step(step: Step, calendar, callback) -> tuple[StepResult, set[str]]:
    minutes_fn = getattr(calendar, "billable_minutes", None)
    minutes = minutes_fn(step.start, step.end) if minutes_fn is not None else 0
    try:
        result, blocked = callback(step, minutes=minutes)
    except Exception:
        return StepResult.empty(step.id), set()
    return result, blocked
```

**1-Hop Verify & Fix (Good):** Verify each collaborator's 1-hop contract independently: inspect `calendar`'s governing `BillingCalendar` protocol and immediate concrete producer to confirm `billable_minutes` is required, and inspect `callback`'s callable type annotation or immediate production supplier to confirm it returns `(StepResult, set)` without raising expected control-flow exceptions (suppressing either candidate if its own contract is unresolved). Call `calendar.billable_minutes(...)` and unpack `callback(...)` directly, and configure test doubles (`MagicMock(return_value=(StepResult(...), set()))` or a complete stub) to match the real contracts.

```python
def run_step(
    step: Step, calendar: BillingCalendar, callback
) -> tuple[StepResult, set[str]]:
    minutes = calendar.billable_minutes(step.start, step.end)
    result, blocked = callback(step, minutes=minutes)
    return result, blocked


def test_run_step_forwards_billable_minutes():
    step = Step(id="S1", start=8, end=14)
    calendar = StubBillingCalendar(minutes=360)
    callback = MagicMock(return_value=(StepResult(step_id="S1", minutes=360), set()))
    result, blocked = run_step(step, calendar, callback)
    callback.assert_called_once_with(step, minutes=360)
    assert result.minutes == 360
    assert blocked == set()
```

**Counterweight (When to Keep):** Open plugin architectures where external extensions may omit optional hook methods, or documented extension boundaries designed to isolate untrusted third-party callback failures from crashing the host process, legitimately require capability checks and boundary exception handling.

### Runtime `_sync_runtime_hooks()` trampolines and `_DEFAULT_*` identity checks

**Pattern Name & Default Grade Hint:** **Zombie Scaffolding** — runtime monkeypatch trampoline — `[Hard]`, **Half-Moves & Zombie Scaffolding**.

**Signal (Bad):** A facade module runs `_sync_runtime_hooks()` on every call to copy monkeypatched facade attributes into submodule globals, or compares `if stop_worker is not _DEFAULT_STOP_HOOK:` in production (`PY10b_test_smell`) solely to detect whether a test monkeypatched `stop_worker`.

```python
_DEFAULT_STOP_HOOK = stop_worker


def _sync_runtime_hooks() -> None:
    _worker_mod.stop_worker = stop_worker


def dispatch_order(order: Order) -> str:
    _sync_runtime_hooks()
    if stop_worker is not _DEFAULT_STOP_HOOK:
        stop_worker(order.id)
    receipt = _worker_mod.process(order)
    return f"dispatched:{receipt.id}"
```

**1-Hop Verify & Fix (Good):** Inspect the immediate definitions and production references of `_sync_runtime_hooks()` and `_DEFAULT_STOP_HOOK` to confirm they serve only test monkeypatching rather than a live runtime replacement contract (dismissing non-callable `_DEFAULT_*` value comparisons). Delete `_sync_runtime_hooks()` and `_DEFAULT_*` identity checks from production, and patch collaborators at the module namespace where `dispatch_order` looks them up (or pass explicit dependency parameters).

```python
def dispatch_order(order: Order) -> str:
    receipt = _worker_mod.process(order)
    return f"dispatched:{receipt.id}"
```

**Counterweight (When to Keep):** Explicit dependency-injection parameters (`stop_hook: Callable[[str], None] = stop_worker`), documented callback registries, or supported runtime hook replacement APIs used by production callers are valid extension seams.

### Self-patched SUT tests mocking the directly invoked function

**Pattern Name & Default Grade Hint:** **Corruption Test** — self-patched system under test — `[Hard]`, **Implementation-Coupled Tests**.

**Signal (Bad):** A test patches `orders.dispatch_order` with a canned `return_value` while directly invoking `orders.dispatch_order(...)` inside the active patch (`PY10b_test_smell`), asserting on the mock's return value instead of executing the system under test.

```python
def test_dispatch_order_calls_patched_target():
    with patch.object(
        orders, "dispatch_order", return_value="dispatched:O-1"
    ) as mocked:
        assert orders.dispatch_order(Order(id="O-1")) == "dispatched:O-1"
        mocked.assert_called_once()
```

**1-Hop Verify & Fix (Good):** Inspect the `patch` target and the call site in the test to confirm the active patch replaces the exact binding being invoked (since `PY10b_test_smell` matches terminal names) and bypasses the real implementation. Invoke `orders.dispatch_order` unpatched and mock only the downstream collaborator (`orders._worker_mod.process`) at its lookup namespace.

```python
def test_dispatch_order_formats_worker_receipt():
    order = Order(id="O-1")
    receipt = WorkerReceipt(id="O-1")
    with patch.object(
        orders._worker_mod, "process", return_value=receipt
    ) as process_mock:
        assert orders.dispatch_order(order) == "dispatched:O-1"
        process_mock.assert_called_once_with(order)
```

**Counterweight (When to Keep):** Forwarding spies (`patch.object(..., wraps=...)`, `mocker.spy`) that execute the real implementation while recording call counts, tests that patch a recursive helper to bound recursion depth under a documented seam, and tests where the patched symbol shares a terminal name with a different higher-level orchestrator function are valid patterns.

## 6. Brittle Source-Text & Echo-Chamber Assertions

### Raw source or documentation literal assertions (`read_text` + `in`)

**Pattern Name & Default Grade Hint:** **Corruption Test** — raw source/documentation literal assertion — `[Simplify]`, **Implementation-Coupled Tests** (`[Hard]` when 1-hop diff evidence shows the literal check substitutes for executing changed runtime behavior, locks in dead production code, or makes a false verification claim).

**Signal (Bad):** A test reads a `.py` or `.md` file from disk via `Path(...).read_text()` or `open(...)` and asserts substring membership (`assert "def enforce_workspace_quota" in source_text` or `assert "Quota limit" in guide_text`, flagged by `PY10b_test_smell`) to verify a code change or prose edit without executing runtime behavior.

```python
def test_user_guide_and_validator_mention_quota_limit():
    guide_text = Path("docs/user_guide.md").read_text(encoding="utf-8")
    source_text = Path("app/quotas.py").read_text(encoding="utf-8")
    assert "Quota limit is enforced per workspace" in guide_text
    assert "def enforce_workspace_quota(" in source_text
```

**1-Hop Verify & Fix (Good):** Inspect the immediate subject under test and its diff hunk (do not scan the whole repository for other tests). When the test targets a production function, import and invoke `enforce_workspace_quota` with concrete inputs and assert on its returned value or raised exception; when the test only pins ordinary prose wording after a documentation edit, remove the `read_text()` literal assertion.

```python
def test_enforce_workspace_quota_rejects_excess_usage():
    workspace = Workspace(id="W1", limit=10, used=10)
    with pytest.raises(QuotaExceededError):
        enforce_workspace_quota(workspace, requested=1)
```

**Counterweight (When to Keep):** Preserve text assertions for (1) code generators, template compilers, schema/migration exporters, CLI `--help` output, and golden-file/snapshot suites whose output under test is generated source or rendered text; (2) AST/static-analysis tools (including the **Candidate Extractor** `scan_python.py`) that feed synthetic source fixtures and assert on structured diagnostics; and (3) externally required verbatim legal, license, security, or normative specification strings.

### Hardcoded constant echo-chamber assertions

**Pattern Name & Default Grade Hint:** Constant echo-chamber assertion — `[Simplify]`, **Implementation-Coupled Tests** (for the test echoing a constant) / **Dead Plumbing** (for the unconsumed constant dictionary); `[Hard]`, **Error Washing**, when a hardcoded constant falsely claims verification that never occurred, or `[Hard]`, **Split Paths & Compensation**, when a worker discards an archived admission snapshot and recomputes with wall-clock `date.today()`.

**Signal (Bad):** A test repeats a hardcoded bookkeeping dictionary (`"audit_stamp": {"facts_verified": True, "baseline_matched": True, "verified_at": "admission"}`) that the producer emits unconditionally without any dynamic branch or consumer—and in the worst case, the producer claims `"verified_at": "admission"` while discarding `run.accepted_decisions` and recomputing readiness with wall-clock `current_date()` (`date.today()`).

```python
def build_comparison_view(run: ArchivedRun) -> dict:
    decisions = classify_items(run.items, today=current_date())
    return {
        "decisions": decisions,
        "audit_stamp": {
            "facts_verified": True,
            "baseline_matched": True,
            "verified_at": "admission",
        },
    }


def test_comparison_view_includes_audit_stamp(archived_run):
    view = build_comparison_view(archived_run)
    assert view["audit_stamp"] == {
        "facts_verified": True,
        "baseline_matched": True,
        "verified_at": "admission",
    }
```

**1-Hop Verify & Fix (Good):** Verify each finding against its immediate 1-hop contract: inspect `build_comparison_view` and its response schema/caller to confirm `audit_stamp` is an unconsumed constant, and separately inspect `ArchivedRun`'s contract to confirm whether `decisions` must preserve `run.accepted_decisions` archived at admission time (suppressing the snapshot finding if recomputation is intended or unresolved). Remove unconsumed bookkeeping constants and test the real invariant—controlling the builder's clock across the date boundary so a regressed wall-clock call fails the test.

```python
def build_comparison_view(run: ArchivedRun) -> dict:
    return {"decisions": run.accepted_decisions}


def test_comparison_view_preserves_accepted_decisions_across_dates(monkeypatch):
    run = ArchivedRun(
        admitted_on=date(2026, 10, 29),
        items=[Item(id="item-1", expires_on=date(2026, 10, 29))],
        accepted_decisions={"item-1": "ready"},
    )
    monkeypatch.setattr(views, "current_date", lambda: date(2026, 10, 29))
    assert build_comparison_view(run) == {"decisions": {"item-1": "ready"}}
    monkeypatch.setattr(views, "current_date", lambda: date(2026, 10, 30))
    assert build_comparison_view(run) == {"decisions": {"item-1": "ready"}}
```

**Counterweight (When to Keep):** Asserting protocol version numbers, wire format discriminators (`"version": "v2"`, `"type": "event"`), or static capability descriptors that are constant for a given endpoint but required by external clients, schemas, or routers is a valid contract test.
