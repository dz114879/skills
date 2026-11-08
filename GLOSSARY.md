# Engineering Skills

Reusable agent skills, reference catalogues, and AST candidate extractors for software engineering workflows, multi-axis code and plan review, and anti-defensive coding.

## Language

### Review & Orchestration

**Review Axis**:
An independent evaluation lens run as an isolated parallel reviewer against a shared review scope: a diff (`Standards`, `Spec`, `Lean`) or a Plan (`Reader`, `Implementer`, `Critic`, `Reuse`).
_Avoid_: review stage, review phase

**Goldfish Reviewer**:
A stateless, read-only reviewer subagent that evaluates a review scope (a diff or a Plan) and its referenced contracts without access to the authoring conversation history (or a standalone audit pass applying the same evidence-first rubric).
_Avoid_: fixer agent, inline reviewer

**Elephant**:
The session that wrote a Plan and holds the conversation behind it; it orchestrates the Plan's review and triages the findings.
_Avoid_: main agent, orchestrator

**Plan**:
A pre-implementation markdown document set on disk: an ad-hoc plan, or tickets together with the spec or plan they were cut from (if any).
_Avoid_: design doc, proposal

**Candidate Extractor**:
A stateless, language-specific AST script that locates suspicious code structures and nearby contract evidence within a review scope so a reviewer can verify them, rather than enforcing deterministic lint failures.
_Avoid_: custom linter, ratchet scanner, baseline gate

**Counterweight**:
A legitimate boundary validation, data-loss recovery path, security check, concurrency guarantee, or externally consumed compatibility shim that resembles defensive code structurally and must be preserved.
_Avoid_: whitelist, exception rule

**Public Contract**:
A definition a module declares as its public interface through its export surface (an exported name, or a public method of an exported class), together with its documented signature and behavior. Callers outside the reviewed code may rely on it, so it needs no internal caller to stay live.
_Avoid_: export obligation, exported symbol

**Lint-Owned**:
A candidate whose exact shape a standard linter or typechecker rule already reports on the same node, and whose defect that rule's prescribed fix fully resolves.
_Avoid_: linter overlap, too-fine

**Semantic Fidelity**:
The requirement that normalization, fallback, coalescing, or cleanup preserves distinct domain values (such as `0`, `""`, `[]`, `False`, or explicit `None` clear vs omitted key) without collapsing them into a lossy default.
_Avoid_: lenient normalization

### Plan Review Findings

**Open Decision**:
A design choice a Plan leaves to its implementer that the Plan should have made: an interface or seam shape, data shape, error semantics, ownership, external contract, behavioural alternative, test seam, or ordering constraint.
_Avoid_: ambiguity, underspecification

**False Premise**:
A claim in a Plan about the current system that the repo contradicts, or a reference to something that does not exist.
_Avoid_: hallucination, drift

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

### Documentation Audit

**Documentation Maintenance Priority**:
The evidence-backed order in which documents should be reviewed and considered for refactoring or improvement. Navigation structure and observed access inform this order; popularity alone does not establish document quality or defect severity.
_Avoid_: agent preference, attention score

**Document Review Queue**:
An ordered set of documents awaiting review, prioritized by observed access and navigation importance to allocate review effort. An unreviewed document has unknown review outcomes, not an implied clean bill of health.
_Avoid_: fix queue, document quality ranking

**Documentation Improvement Queue**:
An ordered set of confirmed documentation findings, prioritized by their consequences, with affected scope informing relative urgency. It guides subsequent maintenance without authorizing changes to the documents.
_Avoid_: popularity ranking, review candidate queue

**Maintained Development Documentation**:
Documentation intended to remain useful across development tasks and to evolve with the codebase. Its role and expected lifetime distinguish it from task-local records, regardless of its directory name.
_Avoid_: all Markdown, frequently read documentation

**Task-Local Documentation**:
Working records whose primary purpose is to support a particular task or bounded effort rather than provide continuing development guidance. Retaining these records does not by itself make them maintained development documentation.
_Avoid_: useless documentation, dead documentation

**Expected Context Entry**:
A document expected to enter an agent's context under the selected tool's loading rules and applicable scope. This expectation is distinct from evidence that a particular historical session loaded it.
_Avoid_: confirmed historical read, automatic preference

**Navigation Entry**:
A document explicitly selected as a starting point for exploring development guidance. Selection establishes a navigation root, not automatic context loading.
_Avoid_: automatically loaded README

**Exploration Trial**:
A bounded, read-only investigation of a representative development task used to observe which files an agent accesses under the current environment. It measures implementation preparation, not historical usage or the entire implementation lifecycle.
_Avoid_: historical session sample, full implementation simulation

**Observed File Access**:
A file access supported by an attributable tool invocation and result. An agent's claim to have read a file is not sufficient evidence of observed access.
_Avoid_: self-reported reading, path mention
