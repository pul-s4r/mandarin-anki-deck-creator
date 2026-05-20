# Monorepo packages

| Package | Path | Install (after migration) |
|---------|------|-------------------------|
| `anki-pipeline-core` | `core/` | `pip install -e "packages/core[dev]"` |
| `anki-deck-generator` | `generator/` | `pip install -e "packages/generator[dev]"` |
| `vocab-review-agent` | `review-agent/` | `pip install -e "packages/review-agent[dev]"` |

During migration, the generator may still install from the repository root. See `docs/core-library-extraction.md`.
