"""`state` subcommand handler."""

from __future__ import annotations

import argparse
from pathlib import Path

from anki_deck_generator.state.sqlite_store import SqliteStateStore


def run_state_command(args: argparse.Namespace) -> int:
    db = Path(args.db_path).resolve()
    if args.state_command == "init":
        store = SqliteStateStore(db)
        store.init_schema()
        store.close()
        print(f"initialized state database at {db}")
        return 0
    if args.state_command == "list-cards":
        store = SqliteStateStore(db)
        try:
            rows = list(store.iter_all_cards())
        finally:
            store.close()
        if not rows:
            print("(no cards)")
            return 0
        for r in rows:
            m = (r.meaning or "")[:48]
            print(f"{r.simplified}\t{r.card_id[:8]}…\t{m}")
        return 0
    if args.state_command == "list-runs":
        store = SqliteStateStore(db)
        try:
            runs = list(store.iter_runs(limit=50))
        finally:
            store.close()
        if not runs:
            print("(no runs)")
            return 0
        for rr in runs:
            print(f"{rr.run_id}\t{rr.trigger}\t{rr.started_at}\tjson_bytes={len(rr.sync_report_json)}")
        return 0
    return 1
