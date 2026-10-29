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

### Required model fields disguised as optional

**Pattern Name & Default Grade Hint:** Required-field reflection fallback — `[Simplify]`, **Revalidation**.

**Signal (Bad):** An admission boundary guarantees a field, but a partial internal object makes downstream code pretend the field may be absent.

```python
from types import SimpleNamespace


def project(accepted):
    item = SimpleNamespace(code=accepted.code)
    return describe(item)


def describe(item):
    return getattr(item, "category", None)
```

**1-Hop Verify & Fix (Good):** Inspect the admitted model's definition and constructor. If `category` is required there, carry the complete model through instead of reconstructing a partial duck type. Align internal field annotations with that guarantee and remove only the now-unreachable missing-field branches and error counters.

```python
def project(accepted: AcceptedItem):
    return describe(accepted)


def describe(item: AcceptedItem):
    return item.category
```

**Counterweight (When to Keep):** Sparse external payloads, public partial-update models, and versioned plugin records can have genuinely absent fields. Verify the admission guarantee rather than promoting a convention into a required-field contract.

### Required protocol methods probed for incomplete stubs

**Pattern Name & Default Grade Hint:** Test-stub capability fallback — `[Hard]`, **Half-Moves & Zombie Scaffolding**, when it masks a broken test double and sustains a dead production path.

**Signal (Bad):** All production implementations promise the method; incomplete stubs alone exercise the fallback and its diagnostic counters.

```python
def publish(sender, message):
    emit = getattr(sender, "emit", None)
    if emit is None:
        return {"sent": False, "unsupported": True}
    return emit(message)
```

**1-Hop Verify & Fix (Good):** Inspect the governing sender protocol and the supplied stub. If `emit` is required, call it directly, complete the stub's implementation, and remove diagnostics that only describe the test-only fallback.

```python
def publish(sender: Sender, message):
    return sender.emit(message)


class StubSender:
    def emit(self, message):
        return {"sent": True}
```

**Counterweight (When to Keep):** An open plugin protocol with optional `emit`, dynamic adapter, or documented older external consumer can legitimately lack the capability. Preserve negotiation when that contract is live.
