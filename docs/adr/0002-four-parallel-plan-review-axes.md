# Four Parallel Plan Review Axes Over EGM's Sequential Stages

Plan review runs four **Review Axes** (`Reader`, `Implementer`, `Critic`, `Reuse`) as one parallel wave of **Goldfish Reviewers**, instead of the Elephant-Goldfish Model's five sequential stages (Zoom Out → Comprehension → Critic → Intermediate Zoom Out → Implementation Readiness). YAGNI pruning lives inside `Critic` so the same reviewer weighs a failure mode against the complexity its mitigation adds: EGM needs a second Zoom Out stage to prune the defenses its Critic stage introduced, and a single parallel wave cannot sequence that. Fact-checking the Plan's claims about current code lives inside `Implementer`, the reader who would trip over a false claim; reuse and build-vs-buy (including new external dependencies) is its own axis because its cost is search legwork rather than reasoning.

## Considered Options

- **EGM's five sequential stages plus its style and structural reviewers**: rejected as too heavy; style and template conformance carry little signal for fresh-context implementers.
- **One simplicity axis holding YAGNI, reuse, and dependency checks**: rejected because it overloads one reviewer with two kinds of legwork and leaves failure-mode critique unbalanced.
