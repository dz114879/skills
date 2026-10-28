# Skills

A collection of personal agent skills.

## Layout

```
engineering/
├── code-review-lean/
│   ├── SKILL.md            # Three-axis review: Standards, Spec, and Lean (anti-over-defense)
│   └── agents/
│       └── openai.yaml     # Interface metadata
└── implement-orchestrated/
    ├── SKILL.md            # Orchestrator: ticket/spec implementation + three-axis review
    └── agents/
        └── openai.yaml     # Interface metadata (display name, implicit invocation)
```

## Dependencies

`implement-orchestrated` requires `code-review-lean`, and uses `tdd` or `diagnosing-bugs` when applicable:

- `code-review-lean`
- `tdd` / `diagnosing-bugs` (when applicable)
