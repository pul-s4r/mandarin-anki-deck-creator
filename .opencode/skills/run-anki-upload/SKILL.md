# run-anki-upload

Push cards to desktop Anki via AnkiConnect.

## Trigger Phrases

- "upload to anki"
- "push cards"
- "sync to anki"
- "anki upload"

## Usage

```bash
./scripts/run-anki-upload.sh --source-set <name> --cedict-path <path> [options]
```

### Required Arguments

| Argument | Description |
|----------|-------------|
| `--source-set <name>` | Source set name from YAML config |
| `--cedict-path <path>` | Path to cedict_ts.u8 |

### Optional Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `--source-set-config <yaml>` | Path to YAML config | `$SOURCE_SET_CONFIG` or `~/Documents/anki-pipeline-test-state.yaml` |
| `--state-db <path>` | SQLite state DB path | `$STATE_DB` or `~/.local/share/anki-notes-pipeline/state.db` |
| `--anki-deck-name <name>` | Target Anki deck | From YAML config |
| `--anki-model-name <name>` | Anki note type | `Chinese vocabulary` |
| `--anki-connect-url <url>` | AnkiConnect URL | `http://127.0.0.1:8765` |

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SOURCE_SET_CONFIG` | Default source set config path | `~/Documents/anki-pipeline-test-state.yaml` |
| `STATE_DB` | Default state DB path | `~/.local/share/anki-notes-pipeline/state.db` |

## Examples

```bash
# Push to default deck
./scripts/run-anki-upload.sh --source-set demo-anki --cedict-path /path/to/cedict_ts.u8

# Push to specific deck
./scripts/run-anki-upload.sh --source-set demo --cedict-path /path/to/cedict_ts.u8 --anki-deck-name "Chinese::301"

# Custom config and state DB
./scripts/run-anki-upload.sh --source-set my-notes \
    --source-set-config ~/Documents/my-sources.yaml \
    --state-db ~/Documents/my-state.db \
    --cedict-path /path/to/cedict_ts.u8
```

## What It Does

1. Checks AnkiConnect is responding
2. Runs `anki-notes-pipeline schedule --to-anki`
3. Processes sources (local files and/or Google Drive)
4. Pushes vocabulary cards to desktop Anki via AnkiConnect
5. Syncs with AnkiWeb if `auto_sync: true`

## Prerequisites

1. **Install Anki** with AnkiConnect plugin
2. **Start Anki** (AnkiConnect listens on port 8765 by default)
3. **Configure source set** with `type: anki` exporter OR use `--anki-deck-name`
4. `pip install -e ".[sync,ankiweb,google-drive]"`
5. AWS credentials configured (for Bedrock LLM calls)
6. CEDICT file (`cedict_ts.u8`)

## Source Set Config for Anki

See `scripts/sources.yaml.example` for the full schema.

Example YAML with Anki exporter:

```yaml
schema_version: 1
source_sets:
  demo-anki:
    sources:
      - provider: local-filesystem
        path: ~/Documents/notes.pdf
    exporters:
      - type: anki
        deck_name: "Chinese::301"
        model_name: "Chinese vocabulary"
        anki_connect_url: "http://127.0.0.1:8765"
        conflict_policy: "prefer-remote"
        auto_create_deck: true
        auto_create_model: true
        auto_sync: true
```

## AnkiConnect Options

| Option | Values | Description |
|--------|--------|-------------|
| `conflict_policy` | `prefer-remote`, `prefer-local`, `tag-and-skip` | How to handle conflicts between local and Anki cards |
| `auto_create_deck` | `true`, `false` | Create deck if it doesn't exist |
| `auto_create_model` | `true`, `false` | Create note type if it doesn't exist |
| `auto_sync` | `true`, `false` | Sync with AnkiWeb after changes |

## Notes

- **Not included in standard run instructions** — run manually when needed
- Checks AnkiConnect before proceeding
- Prompts for confirmation if AnkiConnect is not responding
- Uses your personal config at `~/Documents/anki-pipeline-test-state.yaml` by default
- Template config: `scripts/sources.yaml.example`
