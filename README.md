# Mandarin Anki deck generator

Pipeline: PDF / Markdown / DOCX → plain text → Amazon Bedrock (LangChain) → CC-CEDICT enrichment → vocabulary CSV for Anki.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

```bash
cp .env_SAMPLE .env
```

Fill in AWS credentials for Bedrock (e.g. `AWS_BEARER_TOKEN_BEDROCK`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`) in your actual `.env` file.

Download CC-CEDICT (`cedict_ts.u8`) from [MDBG CC-CEDICT](https://www.mdbg.net/chinese/dictionary?page=cc-cedict) and pass `--cedict-path`.

## Usage

```bash
anki-notes-pipeline run /path/to/notes.pdf --output out.csv --cedict-path /path/to/cedict_ts.u8
```

Options: `--chunk-size`, `--chunk-overlap`, `--csv-bom`, `--skip-lines-filter`, model params via environment (see `anki_deck_generator.config.settings`).

## Tests

```bash
pytest
```
