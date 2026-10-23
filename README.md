# Skills

A collection of personal agent skills.

## Layout

```
engineering/
├── code-review-lean/
│   ├── SKILL.md            # Three-axis review: Standards, Spec, and Lean (anti-over-defense)
│   ├── agents/
│   │   └── openai.yaml     # Interface metadata
│   └── references/
│       ├── lean-python.md  # Python-specific anti-over-defense patterns (loaded on demand)
│       └── lean-sql.md     # SQL/database anti-over-defense patterns (loaded on demand)
└── implement-orchestrated/
    ├── SKILL.md            # Orchestrator: single-ticket implementation + three-axis review
    └── agents/
        └── openai.yaml     # Interface metadata (display name, implicit invocation)
```

## Dependencies

`implement-orchestrated` requires the following skills to be installed and callable:

- `tdd`
- `code-review-lean`
