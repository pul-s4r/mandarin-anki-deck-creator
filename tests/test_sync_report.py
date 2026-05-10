from __future__ import annotations

from datetime import UTC, datetime

from anki_deck_generator.sync.report import SyncReport, SyncReportStats, SyncRunOutcome


def test_sync_report_json_is_stable() -> None:
    t = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)
    r = SyncReport(
        run_id="abc",
        run_started_at=t,
        run_finished_at=t,
        outcomes=[SyncRunOutcome(source_id="s", external_id="/x", skipped_document=True)],
        stats=SyncReportStats(documents_skipped=1),
    )
    j1 = r.to_json()
    j2 = r.to_json()
    assert j1 == j2
