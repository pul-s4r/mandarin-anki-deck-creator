# run-mocked

Run the pipeline with a mocked LLM fixture (deterministic, no AWS calls).

## Trigger Phrases

- "mocked run"
- "test pipeline"
- "run mocked"
- "mocked pipeline"

## Usage

```bash
./scripts/run-mocked.sh --input <file> --output <csv> [options]
```

### Required Arguments

| Argument | Description |
|----------|-------------|
| `--input <file>` | Input PDF, Markdown, or DOCX file |
| `--output <csv>` | Output CSV path |

### Optional Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `--cedict-path <path>` | Path to cedict_ts.u8 | None |
| `--fixture-path <path>` | Path to LLM fixture JSON | `tests/baselines/llm_mock.json` |

## Examples

```bash
# Basic usage with sample input
./scripts/run-mocked.sh --input tests/baselines/inputs/sample.pdf --output /tmp/out.csv

# With CEDICT enrichment
./scripts/run-mocked.sh --input notes.md --output out.csv --cedict-path /path/to/cedict_ts.u8

# Custom fixture
./scripts/run-mocked.sh --input notes.pdf --output out.csv --fixture-path /path/to/custom-fixture.json
```

## What It Does

1. Runs `anki-notes-pipeline run` with `--llm-fixture-path`
2. Uses canned vocabulary items from the fixture JSON
3. No AWS Bedrock calls — fully deterministic
4. Optional CEDICT enrichment for pinyin/meanings

## Prerequisites

- `pip install -e ".[dev]"`
- CEDICT file (optional, for enrichment)

## Fixture Format

The LLM fixture JSON maps chunk SHA-256 hashes to vocabulary items:

```json
{
  "chunks": {
    "<chunk-hash>": [
      {
        "simplified": "苹果",
        "traditional": "",
        "pinyin": "",
        "meaning": "apple",
        "part_of_speech": "noun",
        "usage_notes": ""
      }
    ]
  },
  "translations": {}
}
```

## Notes

- Default fixture: `tests/baselines/llm_mock.json` (contains "苹果" → "apple")
- Sample inputs: `tests/baselines/inputs/sample.{pdf,md,docx}`
- CEDICT sample: `tests/baselines/cedict_sample.u8`
