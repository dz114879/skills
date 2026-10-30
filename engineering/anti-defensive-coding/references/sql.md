# SQL / Storage Catalogue

## 1. Aggregate Scalar Guarantees

### Rechecking a guaranteed count row

**Pattern Name & Default Grade Hint:** Aggregate scalar hedging — `[Simplify]`, **Revalidation**.

**Signal (Bad):** Python supplies missing-row, null-cell, and falsy defaults for a query that already guarantees one non-null integer scalar.

```python
row = connection.execute("SELECT COUNT(*) FROM records WHERE active = 1").fetchone()
count = int((row[0] if row and row[0] is not None else 0) or 0)
```

**1-Hop Verify & Fix (Good):** Read the producing query and row adapter. A simple top-level ungrouped `SELECT COUNT(*)` returns one non-null integer row, including when its `WHERE` matches nothing, provided there is no `HAVING`, `LIMIT 0`, non-zero `OFFSET`, or outer filtering. Consume that scalar directly. An ungrouped `COALESCE(MAX(integer_column), 0)` can also establish a non-null integer scalar if the column domain and row-cardinality conditions are verified.

```python
count = connection.execute(
    "SELECT COUNT(*) FROM records WHERE active = 1"
).fetchone()[0]
```

**Counterweight (When to Keep):** `SUM`, `MAX`, and `MIN` can return `NULL` on an empty input, even for a `NOT NULL` column. `GROUP BY` can return zero or multiple rows; `HAVING`, `LIMIT 0`, non-zero `OFFSET`, and an outer filtering query can eliminate the aggregate row. Preserve absent-row and nullable-value handling where those contracts require it, and preserve the distinction between “no value” and zero. A custom adapter that changes row shape/cardinality requires its own contract proof.

## 2. Row Mapping & Storage-Integer Fidelity

### `from_row` swallowing `KeyError` across divergent `SELECT` projections

**Pattern Name & Default Grade Hint:** Missing-column `KeyError` washing in `from_row` — `[Hard]`, **Error Washing** (when missing required columns are defaulted into silent `None`/`""`) or **Split Paths & Compensation** (when `from_row` compensates for divergent `SELECT` column sets across callers).

**Signal (Bad):** A model's `from_row` uses a `KeyError`-swallowing helper (`row.get(key, default)` or `try: row[key] except (KeyError, IndexError): return default`) so one broad model constructor can absorb divergent `SELECT` column sets and dead model fields—silently turning omitted SQL columns (`published_rev`, `notes`) into `None` or `""`.

```python
def read_col(row, key: str, default=None):
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


class ReleaseSummary:
    @classmethod
    def from_row(cls, row) -> "ReleaseSummary":
        return cls(
            release_id=row["release_id"],
            published_rev=read_col(row, "published_rev"),
            notes=read_col(row, "notes", ""),
            legacy_tag=read_col(row, "legacy_tag"),
        )
```

**1-Hop Verify & Fix (Good):** Inspect the immediate `SELECT` queries feeding `from_row` and the model's required-field contract. Index `row["col"]` directly so missing columns fail immediately as schema or query-projection bugs, and align `SELECT` column lists across the queries feeding that model (or introduce a narrow value object for a genuinely narrow query); when 1-hop or extractor reachability evidence also proves a model field (`legacy_tag`) has zero consumers, delete the dead field.

```python
class ReleaseSummary:
    @classmethod
    def from_row(cls, row) -> "ReleaseSummary":
        return cls(
            release_id=row["release_id"],
            published_rev=row["published_rev"],
            notes=row["notes"],
        )
```

**Counterweight (When to Keep):** When reading cross-version archive tables or external database dumps where an older schema version genuinely lacks a column prior to migration, handle the version boundary explicitly in the migration or versioned reader rather than swallowing `KeyError` across all queries.

### `NOT NULL` storage integer parsers with `default=0/1` or lossy byte decoding

**Pattern Name & Default Grade Hint:** Corrupt `NOT NULL` storage integer washing — `[Hard]`, **Error Washing** (**Semantic Fidelity** breach).

**Signal (Bad):** A storage parser for `NOT NULL` integer columns (`revision`, `item_count`) supplies `default=0` or `default=1` on conversion failure, decodes `bytes` with `errors="ignore"`, truncates fractional `Decimal`/`float` values via `int()`, or relies on `str.isdigit()` (which accepts Unicode superscripts like `"²"` that fail `int()`)—turning corrupt stored values (`None`, `"N/A"`, `"1.5"`, corrupt bytes) into valid-looking `0` or `1`.

```python
def decode_stored_int(raw, default: int = 0) -> int:
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        return int(raw)
    except (TypeError, ValueError):
        return default
```

**1-Hop Verify & Fix (Good):** Inspect the table DDL (`NOT NULL` vs nullable column, signed vs non-negative domain) and the row-adapter/converter contract. Parse required values strictly according to that contract and raise a field-specific `ValueError` on corrupt inputs (omitting any `default` parameter on `NOT NULL` decoders; a nullable column decoder returns `None` only for SQL `NULL` and still raises `ValueError` on corrupt values). In the non-negative integer/decimal-string contract below, accept `int` excluding `bool` and ASCII decimal strings via `str.isdecimal()` (if the adapter legitimately supplies signed strings or `bytes`, decode UTF-8 strictly and parse signed decimal strings without `errors="ignore"` or fallback defaults).

```python
def decode_required_int(raw: object, *, field: str) -> int:
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    if isinstance(raw, str) and raw.isdecimal():
        return int(raw)
    raise ValueError(f"Corrupt integer in NOT NULL column {field}: {raw!r}")
```

**Counterweight (When to Keep):** User-facing query string parameters or optional configuration inputs at an external HTTP/CLI boundary may legitimately substitute a documented default when the parameter is omitted (`raw is None`), while still rejecting malformed non-empty strings.

## 3. Repository Row Ownership & Copy Churn

### Double-copying fresh repository row mappings

**Pattern Name & Default Grade Hint:** Redundant row-dict copy churn — `[Simplify]`, **Redundant Churn**.

**Signal (Bad):** A repository base helper (`self.fetchall(...)` / `self.fetchone(...)`) already materializes fresh `dict` instances from `sqlite3.Row`, and the calling repository method or service immediately wraps the result in `[dict(row) for row in self.fetchall(...)]` or `dict(row)`.

```python
class RowStore:
    def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        cursor = self.conn.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]


class InvoiceStore(RowStore):
    def list_open(self) -> list[dict]:
        rows = self.fetchall("SELECT invoice_id, total_cents FROM invoices WHERE paid = 0")
        return [dict(row) for row in rows]
```

**1-Hop Verify & Fix (Good):** Inspect `self.fetchall` / `self.fetchone`. When the base helper already returns freshly allocated `dict` objects with no shared aliasing, return or iterate the list directly.

```python
class InvoiceStore(RowStore):
    def list_open(self) -> list[dict]:
        return self.fetchall("SELECT invoice_id, total_cents FROM invoices WHERE paid = 0")
```

**Counterweight (When to Keep):** Keep `[dict(row) for row in cursor.fetchall()]` at the single raw cursor boundary where mapping-like driver row objects (`sqlite3.Row` or cursor-backed lazy mappings; positional tuple rows require a `row_factory` or pairing with `cursor.description`) are converted into owned `dict` instances, or when caching a baseline list that callers will mutate in place across multiple passes.

## 4. Snapshot Consistency, Metadata Queries & Relational Closure

### Same-snapshot probe-then-fetch and repeated `PRAGMA` metadata queries

**Pattern Name & Default Grade Hint:** Same-snapshot redundant query / metadata re-probe — `[Simplify]`, **Redundant Churn** (when re-fetching data or metadata) or **Revalidation** (when rechecking existence/schema guarantees already established in the same snapshot).

**Signal (Bad):** Within a single read snapshot or write transaction, code probes a table by key to get an ID (`find_by_token`) and immediately queries the same entity again (`get_state(shipment_id)`), re-runs quota/existence checks just verified in the same transaction, or executes `PRAGMA table_info(t)`, `PRAGMA index_list(t)`, or `SELECT sql FROM sqlite_master` more than once for the same table inside one schema check while the schema is unchanged.

```python
def load_shipment_state(conn, token: str) -> dict | None:
    probe = conn.execute(
        "SELECT shipment_id FROM shipments WHERE request_token = ?", (token,)
    ).fetchone()
    if probe is None:
        return None
    row = conn.execute(
        "SELECT shipment_id, status, total_cents FROM shipments WHERE shipment_id = ?",
        (probe[0],),
    ).fetchone()
    return {"shipment_id": row[0], "status": row[1], "total_cents": row[2]}


def verify_ledger_table(conn) -> None:
    if not conn.execute("PRAGMA table_info(ledger_entries)").fetchall():
        raise RuntimeError("Missing ledger_entries")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ledger_entries)").fetchall()}
    if "amount_cents" not in cols:
        raise RuntimeError("Missing amount_cents")
    indexes = conn.execute("PRAGMA index_list(ledger_entries)").fetchall()
    if not indexes:
        raise RuntimeError("Missing indexes")
```

**1-Hop Verify & Fix (Good):** Inspect the enclosing transaction/connection scope. Merge same-entity probe-and-fetch into one query (or reuse the row returned by the first query), and query `PRAGMA table_info(t)` / `sqlite_master` once per schema verification scope and pass the metadata collection to downstream checks.

```python
def load_shipment_state(conn, token: str) -> dict | None:
    row = conn.execute(
        "SELECT shipment_id, status, total_cents FROM shipments WHERE request_token = ?",
        (token,),
    ).fetchone()
    if row is None:
        return None
    return {"shipment_id": row[0], "status": row[1], "total_cents": row[2]}


def verify_ledger_table(conn) -> None:
    columns = conn.execute("PRAGMA table_info(ledger_entries)").fetchall()
    if not columns or "amount_cents" not in {row[1] for row in columns}:
        raise RuntimeError("Invalid ledger_entries columns")
    if not conn.execute("PRAGMA index_list(ledger_entries)").fetchall():
        raise RuntimeError("Missing indexes")
```

**Counterweight (When to Keep):** Across separate autocommit statements or `READ COMMITTED` queries where concurrent writers can commit between reads and the caller requires observing the later committed state, or after executing DDL (`ALTER TABLE`, `CREATE INDEX`) or data mutations within the transaction, preserve the later query rather than reusing a stale prior result.

### Partial-slice query feeding a full relational closure check

**Pattern Name & Default Grade Hint:** Partial-slice closure validation bug — `[Hard]`, **Split Paths & Compensation** (**Semantic Fidelity** breach).

**Signal (Bad):** A query filters rows to the local subset being edited (`WHERE workspace_id IN (...)` or `WHERE parent_stage_id IS NULL`) and passes that partial slice into an invariant checker that validates a global relational closure—such as verifying that a shared `bundle_id` never spans multiple workspaces, or that two top-level stages (`parent_stage_id IS NULL`) are adjacent when a filtered-out child stage (`parent_stage_id IS NOT NULL`) sits between them—silently missing conflicts with unqueried rows.

```python
def check_bundle_workspace_isolation(conn, edited_workspaces: list[str]) -> None:
    placeholders = ",".join("?" for _ in edited_workspaces)
    rows = conn.execute(
        f"SELECT bundle_id, workspace_id FROM tasks WHERE workspace_id IN ({placeholders})",
        tuple(edited_workspaces),
    ).fetchall()
    by_bundle: dict[str, set[str]] = {}
    for bundle_id, workspace_id in rows:
        by_bundle.setdefault(bundle_id, set()).add(workspace_id)
    for bundle_id, workspaces in by_bundle.items():
        if len(workspaces) > 1:
            raise ValueError(f"Bundle {bundle_id} spans multiple workspaces")
```

**1-Hop Verify & Fix (Good):** Inspect the domain invariant in the validator, the `WHERE` clause in the producing query, and the column collation/canonicalization rules in the schema. When the invariant ranges over an equivalence class or sequence (`bundle_id` membership across all workspaces, or the complete stage order), query the complete relational closure—every row sharing any touched `bundle_id` (or evaluate the aggregate closure directly in SQL so the column's collation governs equality).

```python
def check_bundle_workspace_isolation(conn, edited_workspaces: list[str]) -> None:
    placeholders = ",".join("?" for _ in edited_workspaces)
    conflicts = conn.execute(
        f"""
        SELECT bundle_id
        FROM tasks
        WHERE bundle_id IN (
            SELECT DISTINCT bundle_id FROM tasks WHERE workspace_id IN ({placeholders})
        )
        GROUP BY bundle_id
        HAVING COUNT(DISTINCT workspace_id) > 1
        """,
        tuple(edited_workspaces),
    ).fetchall()
    if conflicts:
        raise ValueError(f"Bundle {conflicts[0][0]} spans multiple workspaces")
```

**Counterweight (When to Keep):** Purely row-local invariants, or schemas where global bundle ownership is already enforced by DDL (for example, `bundles(bundle_id PRIMARY KEY, workspace_id NOT NULL)` referenced via an enforced composite foreign key `FOREIGN KEY (bundle_id, workspace_id) REFERENCES bundles(bundle_id, workspace_id)` with `PRAGMA foreign_keys = ON`), only need the affected partition slice.

## 5. Transaction & Savepoint Boundaries

### Per-row nested `SAVEPOINT` in batch loops and assertion-then-savepoint

**Pattern Name & Default Grade Hint:** Redundant nested `SAVEPOINT` / transaction wrapper — `[Simplify]`, **Redundant Churn** (or **Revalidation** when repeating per-row schema/precondition checks); `[Hard]` when misplaced transaction/savepoint scopes break atomicity, rollback boundaries, or lock lifecycles under batch load.

**Signal (Bad):** A batch entry point opens a transaction and validates the batch, then loops over items calling a single-item command method or opening a per-row nested `with self.tx.savepoint():` (`SAVEPOINT`) that repeats schema/precondition checks per row; or a helper inside an caller-owned transaction scope asserts `if not self.conn.in_transaction: raise ...` on line 1 and immediately opens a nested `with self.tx.savepoint():` / `transaction()` on line 2 when the entire outer transaction always rolls back as one unit.

```python
class LedgerWriter:
    def record_batch(self, entries: list[dict]) -> None:
        with self.tx.transaction():
            for entry in entries:
                with self.tx.savepoint():
                    self.record_single(entry)

    def record_single(self, entry: dict) -> None:
        with self.tx.transaction():
            self._insert_entry(entry)

    def confirm_batch(self, batch_id: str) -> None:
        if not self.conn.in_transaction:
            raise RuntimeError("confirm_batch requires an active transaction")
        with self.tx.transaction():
            self.conn.execute("UPDATE batches SET confirmed = 1 WHERE id = ?", (batch_id,))
```

**1-Hop Verify & Fix (Good):** Inspect the resolved transaction/savepoint context manager implementation (`self.tx.transaction` / `savepoint`) and the immediate caller's commit/rollback contract (noting that `sqlite3.Connection.in_transaction` only reports low-level driver state, so verify the caller actually owns the enclosing transaction commit/rollback scope and that nested `transaction()` creates a redundant `SAVEPOINT` with no independent rollback consumer). Extract the transaction-free write primitive (`_insert_entry`) so `record_single` wraps it in one transaction and `record_batch` calls `_insert_entry` directly inside the single outer transaction, and remove the redundant inner wrapper in `confirm_batch`.

```python
class LedgerWriter:
    def record_batch(self, entries: list[dict]) -> None:
        with self.tx.transaction():
            for entry in entries:
                self._insert_entry(entry)

    def record_single(self, entry: dict) -> None:
        with self.tx.transaction():
            self._insert_entry(entry)

    def confirm_batch(self, batch_id: str) -> None:
        if not self.conn.in_transaction:
            raise RuntimeError("confirm_batch requires an active transaction")
        self.conn.execute("UPDATE batches SET confirmed = 1 WHERE id = ?", (batch_id,))
```

**Counterweight (When to Keep):** Preserve a nested `SAVEPOINT` when either the helper or its caller catches an exception around the inner scope to isolate partial failures (rolling back only the failed item or multi-statement helper via the context manager's `__exit__` while keeping the outer transaction alive), or when `in_transaction` only reflects implicit driver state (`autocommit=False`) and the `with self.tx.transaction():` block is the sole owner of `COMMIT` / `ROLLBACK`.

## 6. Batching & Contract Limits

### Caller-side manual chunking that duplicates or bypasses callee batch limits

**Pattern Name & Default Grade Hint:** Caller chunking around callee contract limit — `[Hard]`, **Split Paths & Compensation** (when chunking breaks cross-chunk uniqueness or closure checks); `[Simplify]`, **Revalidation**, when chunking only duplicates validation without state loss.

**Signal (Bad):** A callee enforces `if len(items) > BATCH_LIMIT: raise ...` and deduplicates `seen_keys` within its argument list, while a caller slices `range(0, len(items), BATCH_LIMIT)` in a loop to bypass the limit—either duplicating the callee's validation/deduplication at the call site or breaking cross-chunk uniqueness checks because each chunk validates `seen_keys` in isolation.

```python
BATCH_LIMIT = 500


def prepare_projections(items: list[dict]) -> list[dict]:
    if len(items) > BATCH_LIMIT:
        raise ValueError("Batch exceeds limit")
    seen: set[str] = set()
    for item in items:
        if item["key"] in seen:
            raise ValueError(f"Duplicate key: {item['key']}")
        seen.add(item["key"])
    return [build_projection(item) for item in items]


def restore_all_projections(items: list[dict]) -> list[dict]:
    restored: list[dict] = []
    for offset in range(0, len(items), BATCH_LIMIT):
        chunk = items[offset : offset + BATCH_LIMIT]
        restored.extend(prepare_projections(chunk))
    return restored
```

**1-Hop Verify & Fix (Good):** Inspect the focal chunking call site and the called boundary (`prepare_projections`) to determine whether `BATCH_LIMIT` is an external request-admission cap or a physical resource bound. Converge validation and batching at the owning boundary: validate cross-batch invariants (such as global key uniqueness) once across the full input, and either parameterize `batch_limit: int | None = BATCH_LIMIT` when the limit is purely an external admission cap or chunk only the low-level persistence step after full-input validation.

```python
BATCH_LIMIT = 500


def prepare_projections(
    items: list[dict], *, batch_limit: int | None = BATCH_LIMIT
) -> list[dict]:
    if batch_limit is not None and len(items) > batch_limit:
        raise ValueError("Batch exceeds limit")
    seen: set[str] = set()
    for item in items:
        if item["key"] in seen:
            raise ValueError(f"Duplicate key: {item['key']}")
        seen.add(item["key"])
    return [build_projection(item) for item in items]


def submit_projections(items: list[dict]) -> list[dict]:
    return prepare_projections(items)


def restore_all_projections(items: list[dict]) -> list[dict]:
    return prepare_projections(items, batch_limit=None)
```

**Counterweight (When to Keep):** Chunking expanding SQL `WHERE id IN (?, ...)` or multi-row `VALUES (?, ...)` statements inside a repository method to stay within database bind-parameter limits (such as SQLite's `SQLITE_MAX_VARIABLE_NUMBER`), or chunking writes to bound memory and transaction duration *after* full-input semantic validation has already completed, is a valid physical constraint.

## 7. Write-Lock Critical Section Discipline

### Unconsumed external file I/O or heavy CPU/hashing inside `BEGIN IMMEDIATE` write locks

**Pattern Name & Default Grade Hint:** Write-lock I/O or CPU bleed — `[Hard]`, **Redundant Churn** (when holding an exclusive `BEGIN IMMEDIATE` write lock across external I/O, heavy hashing, or per-row N+1 SQL, or **Error Washing** when pre-check file I/O escapes the storage error boundary); `[Simplify]`, **Redundant Churn**, for minor movable work inside a lock.

**Signal (Bad):** While holding an exclusive database write lock (`BEGIN IMMEDIATE` / `with self.tx.immediate_transaction():`) or outside the storage error boundary before checking database preconditions, code reads external files on disk (populating a receipt field no caller consumes, or failing before a 404 precondition check), computes multi-megabyte `sha256` / compact JSON hashes, builds `.xlsx` exports in memory, or issues per-row N+1 sequence-allocation SQL queries.

```python
def commit_import(self, batch_id: str, rows: list[dict]) -> dict:
    with self.tx.immediate_transaction():
        state_marker = Path(self.marker_path).read_text(encoding="utf-8")
        digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode("utf-8")).hexdigest()
        for row in rows:
            seq = self.repo.reserve_next_seq()
            self.repo.insert_row(batch_id, seq, row, digest)
    return {"batch_id": batch_id, "marker": state_marker}
```

**1-Hop Verify & Fix (Good):** Inspect the resolved lock/transaction context manager, the immediate caller's consumption of the returned receipt, and any precondition/error-boundary scope. Delete file reads whose output (`"marker"`) is unconsumed by the caller (or move a genuinely consumed file read after database precondition checks inside the storage error boundary), hoist pure CPU serialization/hashing (`digest`) before entering `BEGIN IMMEDIATE` (or after closing a read transaction), and allocate sequence numbers in a single batch query (`reserve_seq_range(len(rows))`) inside the write transaction.

```python
def commit_import(self, batch_id: str, rows: list[dict]) -> dict:
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode("utf-8")).hexdigest()
    with self.tx.immediate_transaction():
        sequences = self.repo.reserve_seq_range(len(rows))
        for seq, row in zip(sequences, rows, strict=True):
            self.repo.insert_row(batch_id, seq, row, digest)
    return {"batch_id": batch_id}
```

**Counterweight (When to Keep):** When a hash or external state is derived from rows read inside the write transaction to enforce serializable compare-and-swap, or when an atomic file-rename / lock-file acquisition is the documented coordination protocol paired with the transaction, keep the minimal dependent operation inside the lock boundary.

## 8. Backup Verification, Schema Init & Lock Recovery Safety

### `PRAGMA integrity_check == 'ok'` treated as valid backup or falling through to `schema.sql` initialization

**Pattern Name & Default Grade Hint:** Empty-backup wash via `integrity_check` / first-boot init fallthrough — `[Hard]`, **Error Washing** (**Semantic Fidelity** breach).

**Signal (Bad):** Backup restore or verification treats `PRAGMA integrity_check == "ok"` as sufficient proof that a backup file is valid (SQLite returns `"ok"` on a 0-byte file or an empty database with zero tables) and then calls a shared `init_schema_if_missing(conn)` helper that sees missing tables and runs `executescript(schema.sql)` first-boot initialization—silently turning an empty or truncated backup into a blank initialized database and wiping production data on swap.

```python
def verify_and_prepare_backup(backup_path: Path, schema_sql: str) -> None:
    conn = sqlite3.connect(backup_path)
    status = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if status != "ok":
        raise RestoreError("Corrupt backup file")
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if "accounts" not in tables:
        conn.executescript(schema_sql)
```

**1-Hop Verify & Fix (Good):** Inspect the immediate restore/startup caller and the application's schema/version contract. Separate first-boot initialization from backup/restore verification: on restore or backup validation paths, open the file via an escaped read-only URI, verify non-empty file size, `PRAGMA integrity_check == "ok"`, required application tables, and the expected `schema_meta` version row—raising `RestoreError` if any check fails rather than falling through to `executescript(schema.sql)`.

```python
REQUIRED_TABLES = {"schema_meta", "accounts", "ledger_entries"}
EXPECTED_SCHEMA_VERSION = 1


def verify_backup_database(backup_path: Path) -> None:
    if not backup_path.is_file() or backup_path.stat().st_size == 0:
        raise RestoreError("Backup file is missing or empty")
    uri = f"{backup_path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        status = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if status != "ok":
            raise RestoreError(f"Physical integrity check failed: {status}")
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if not REQUIRED_TABLES.issubset(tables):
            raise RestoreError("Backup is missing required application tables")
        if conn.execute("SELECT version FROM schema_meta").fetchall() != [
            (EXPECTED_SCHEMA_VERSION,)
        ]:
            raise RestoreError("Backup is missing a valid schema_meta version row")
    finally:
        conn.close()
```

**Counterweight (When to Keep):** Explicit first-boot database creation (when opening a brand-new database path in normal startup mode, distinct from backup restore or verification) legitimately initializes `schema.sql` when the database is empty.

### Fail-Open lock file `OSError` deletion during stale-lock self-healing

**Pattern Name & Default Grade Hint:** Fail-Open lock `OSError` self-healing — `[Hard]`, **Error Washing** (violates the lock-safety **Counterweight**).

**Signal (Bad):** A lock-file reader catches `OSError` (permission denied, transient I/O error, or sharing violation) and returns `(None, "read_failed")`, and the caller treats any `None` payload with an old `mtime` as a corrupt/stale lock and unlinks the active lock file (`_unlink_stale_lock`)—failing open and allowing concurrent maintenance or writes while another process holds the lock.

```python
def read_lock_payload(lock_path: Path) -> tuple[dict | None, str]:
    try:
        return json.loads(lock_path.read_text(encoding="utf-8")), "ok"
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        return None, "read_failed"
    except ValueError:
        return None, "corrupt"


def is_maintenance_locked(lock_path: Path, stale_after_seconds: float) -> bool:
    payload, status = read_lock_payload(lock_path)
    if status == "missing":
        return False
    if payload is None:
        age = time.time() - lock_path.stat().st_mtime
        if age > stale_after_seconds:
            lock_path.unlink(missing_ok=True)
            return False
    return True
```

**1-Hop Verify & Fix (Good):** Inspect the lock reader and its caller's recovery branches. Enforce **Fail-Closed** semantics: distinguish `FileNotFoundError` (`"missing"`) from other `OSError` failures (`"read_failed"`), avoid preflight `lock_path.exists()` checks (which return `False` on `PermissionError` in Python and fail open), and treat `"read_failed"` or unverified `"corrupt"` lock files as active/blocked rather than unlinking them by `mtime` age alone.

```python
def is_maintenance_locked(lock_path: Path) -> bool:
    payload, status = read_lock_payload(lock_path)
    if status == "missing":
        return False
    if status in ("read_failed", "corrupt"):
        return True
    return not is_recorded_owner_dead(payload)
```

**Counterweight (When to Keep):** Returning unlocked on `FileNotFoundError` when no lock file exists, or reclaiming a stale lock under a race-safe ownership/fenced-lease protocol after `read_lock_payload` succeeded (`status == "ok"`) and positively verified that the recorded owner process is dead (outside `OSError` read failures), is valid lock recovery.
