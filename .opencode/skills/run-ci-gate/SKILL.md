# run-ci-gate

Run the CI gate test suite for this project.

## Trigger Phrases

- "run tests"
- "ci gate"
- "run ci gate"
- "pytest"
- "run pytest"

## Usage

```bash
./scripts/run-ci-gate.sh
```

## What It Does

1. Installs dependencies: `pip install -e ".[dev,google-drive,ankiweb,server]"`
2. Runs `pytest -q`
3. Exits 0 on success, non-zero on failure

## Prerequisites

- Python 3.12+
- pip

## Notes

- Uses mocked LLM fixtures — no AWS calls
- Uses mocked AnkiConnect — no Anki required
- Uses mocked Google Drive — no OAuth required
- All tests run in disposable temp directories
