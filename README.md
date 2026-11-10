# Skills

A collection of personal agent skills.

## Layout

```
engineering/
├── anti-defensive-coding/
│   ├── SKILL.md            # Read-only Lean anti-over-defense workflow and rubric
│   ├── agents/
│   │   └── openai.yaml     # Interface metadata
│   ├── references/
│   │   ├── python.md      # Python recognition patterns and Counterweights
│   │   ├── sql.md         # SQL/storage recognition patterns and Counterweights
│   │   └── tests.md       # Corruption Test patterns and Counterweights
│   ├── scripts/
│   │   └── scan_python.py # Stateless Python AST Candidate Extractor
│   └── tests/
│       └── test_scan_python.py
├── code-review-lean/
│   ├── SKILL.md            # Three-axis review orchestrator: Standards, Spec, and Lean
│   ├── STANDARDS.md        # Standards axis: documented rules + Fowler smell baseline
│   └── agents/
│       └── openai.yaml     # Interface metadata
├── implement-orchestrated/
│   ├── SKILL.md            # Orchestrator: ticket/spec implementation + three-axis review
│   └── agents/
│       └── openai.yaml     # Interface metadata (display name, implicit invocation)
└── plan-review/
    ├── SKILL.md            # Four-axis fresh-context review of a Plan before implementation
    ├── REVIEWER.md         # Rules every axis reviewer follows
    ├── READER.md           # Reader axis: self-containment via paraphrase
    ├── IMPLEMENTER.md      # Implementer axis: Open Decisions, False Premises, coverage, blocking edges
    ├── CRITIC.md           # Critic axis: premortem and proportion
    ├── REUSE.md            # Reuse axis: build-vs-buy
    └── agents/
        └── openai.yaml     # Interface metadata (display name, implicit invocation)
```

## Dependencies

`code-review-lean` requires subagents and delegates its **Lean** axis to `anti-defensive-coding`.

`implement-orchestrated` requires `code-review-lean`, and uses `tdd` or `diagnosing-bugs` when applicable:

- `code-review-lean` (with `anti-defensive-coding`)
- `tdd` / `diagnosing-bugs` (when applicable)

`plan-review` requires subagents; its Critic axis loads `codebase-design`.

## Development

Python scripts must pass the ruff version locked in `uv.lock` (rules in `pyproject.toml`). Enable the pre-commit gate once per clone:

```sh
git config core.hooksPath .githooks
```

Fix findings with `uv run ruff check --fix` and `uv run ruff format`.
