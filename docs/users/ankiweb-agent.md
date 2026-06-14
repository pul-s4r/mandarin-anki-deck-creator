# Milestone 6 — AnkiWeb agent loop

## Manual verification

1. Install server extras: `pip install -e ".[dev,server,ankiweb]"`
2. Configure env:
   - `ANKI_PIPELINE_STATE_BACKEND=sqlite`
   - `ANKI_PIPELINE_STATE_DB_PATH=/tmp/anki-state.db`
   - `ANKI_SERVER_AGENT_REGISTER_SECRET=dev-secret`
3. Start server: `anki-notes-pipeline serve --host 127.0.0.1 --port 8000`
4. Register agent: `anki-notes-pipeline agent setup --server-url http://127.0.0.1:8000 --agent-id desktop --register-secret dev-secret`
5. Populate cards via `anki-notes-pipeline schedule` or API upload + persistence path.
6. With Anki + AnkiConnect running, confirm agent applies notes tagged `ext_id:<card_id>`.
7. `curl http://127.0.0.1:8000/api/sync/runs/<run_id>` shows `exports_ankiweb`.

## Automated tests

```bash
pip install -e ".[dev,server,ankiweb]"
pytest tests/test_ankiweb_agent_service.py tests/test_ankiweb_web_api.py tests/test_agent_loop.py -v
pytest -q
```
