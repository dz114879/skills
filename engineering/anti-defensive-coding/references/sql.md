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
