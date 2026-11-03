# Modular Python Package and Hybrid JSON Registry Over Rust Rewrite or Custom AST DSL

The Python **Candidate Extractor** (`engineering/anti-defensive-coding/scripts/scan_python.py`) remains a zero-build Python 3.11+ standard-library tool organized as a thin CLI entrypoint delegating to an internal package (`scripts/adc_scanner/`) plus a declarative rule and vocabulary registry (`scripts/adc_scanner/rules.json`). Static rule catalog metadata, `Verify` and `Evidence` templates, and probe signature vocabularies live in `rules.json`, while multi-statement control-flow, scope-bounded AST traversal, and cross-file reachability remain in Python. Profiling the six-target benchmark corpus (`551` Python files, `3,025` candidates in `v0.3`) showed that `ast.parse` takes under `0.4s` total while the `24.5s` wall-clock runtime comes from repeated subtree walks and per-site `ast.unparse` calls (`PY01` `receiver_binding` and `PY10c` `CrossReferences.visit_scope`), which single-pass scope indexing resolves in pure Python without introducing build or distribution steps.

## Considered Options

- **Rust (`ruff_python_parser` / `tree-sitter`) rewrite**: rejected because parsing is `<2%` of runtime; a compiled binary adds cross-platform build and distribution friction and slows down P1/P2 probe and bundling iteration.
- **Full JSON AST query DSL**: rejected as an inner-platform trap; multi-statement control-flow guards (`saved_retry`, `bounded_retry`, `stored_primary_cleanup`, `memo_guard`, `patch_windows`, `CrossReferences`) are clearer, faster, and directly testable in Python.
