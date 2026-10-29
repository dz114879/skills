# Engineering Skills

Reusable agent skills, reference catalogues, and AST candidate extractors for software engineering workflows, multi-axis code review, and anti-defensive coding.

## Language

### Review & Orchestration

**Review Axis**:
An independent evaluation lens (`Standards`, `Spec`, or `Lean`) run as an isolated parallel reviewer against a shared diff scope.
_Avoid_: review stage, review phase

**Goldfish Reviewer**:
A stateless, read-only reviewer subagent that evaluates a diff and its referenced contracts without access to the implementation conversation history (or a standalone audit pass applying the same evidence-first rubric).
_Avoid_: fixer agent, inline reviewer

**Candidate Extractor**:
A stateless, language-specific AST script that locates suspicious code structures and nearby contract evidence within a review scope so a reviewer can verify them, rather than enforcing deterministic lint failures.
_Avoid_: custom linter, ratchet scanner, baseline gate

**Counterweight**:
A legitimate boundary validation, data-loss recovery path, security check, concurrency guarantee, or externally consumed compatibility shim that resembles defensive code structurally and must be preserved.
_Avoid_: whitelist, exception rule

**Semantic Fidelity**:
The requirement that normalization, fallback, coalescing, or cleanup preserves distinct domain values (such as `0`, `""`, `[]`, `False`, or explicit `None` clear vs omitted key) without collapsing them into a lossy default.
_Avoid_: lenient normalization

### Anti-Defensive Coding Patterns

**Error Washing**:
A catch block, fallback, or default substitution that turns an internal bug, missing required key, or unexpected failure into a valid-looking state or silent success, or erases a specific validation/hook rejection diagnostic into a generic failure.
_Avoid_: graceful degradation, defensive error handling

**Moved Defense**:
A redundant guard, fallback, or exception handler deleted from a surviving function in a diff while an equivalent defense is introduced in another function (typically a callee) or behind a dual-mode boolean flag in the same change.
_Avoid_: relocated error handling

**Lossy Coalescing**:
A truthiness fallback (`or` / `||`) or premature default that overwrites valid domain values (`0`, `""`, `[]`, `False`) with a different fallback value, converts missing/null state into silent empty/zero state, or shadows a subsequent required-input check.
_Avoid_: default normalization

**Zombie Scaffolding**:
Legacy entry points, single-line pass-through wrappers, pure re-export modules, or parameters retained after a cutover with no live production consumer.
_Avoid_: backward-compatibility layer (unless backed by a named external consumer)

**Corruption Test**:
A test that constructs impossible internal states, mutates read-only return values, patches the system under test itself, asserts raw source/doc text literals, or calls dead production methods solely to justify defensive or dead code.
_Avoid_: negative test, boundary test
