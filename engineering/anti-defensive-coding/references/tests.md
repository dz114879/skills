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

**Counterweight (When to Keep):** An empty title can be a legitimate domain value or a supported public renderer input. Fuzzing and malformed-input tests belong at a real untrusted boundary; they are valuable when they test that boundary's rejection or recovery contract.

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
def test_rejects_malformed_external_columns(self):
    payload = valid_payload()
    payload["columns"] = 7
    with self.assertRaises(InvalidRecord):
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
def test_rejects_failed_input(self):
    with self.assertRaises(InputFailure):
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
