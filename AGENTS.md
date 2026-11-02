## Agent skills

### Issue tracker

Issues and specs live as local markdown files under `.scratch/`. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context layout (`GLOSSARY.md` at the repo root and ADRs in `docs/adr/`). See `docs/agents/domain.md`.

### Skill developer docs

When planning, refactoring, tuning, or testing `engineering/anti-defensive-coding`, read the matching doc under `.dev/adc/`:
- **Roadmap & phase gates** (planning next iterations or P0–P3 scope) → `.dev/adc/roadmap.md`
- **Architecture, code map, invariants & JSON/CLI contracts** (modifying `scan_python.py` pipeline or schemas) → `.dev/adc/architecture.md`
- **Active probe matrix (`PY01`–`PY10c`), guards & bundling rules** (auditing or changing probe behavior) → `.dev/adc/features.md`
- **Rejected heuristics, retired lint-owned arms & deferred probes** (before proposing any new AST guard or probe) → `.dev/adc/dead-ends.md`
- **Unit test map & `~/adc_scanning_test` benchmark workflow** (running targeted tests or snapshot comparisons) → `.dev/adc/testing.md`
