from __future__ import annotations

import json
from datetime import UTC, datetime
import pytest

from anki_deck_generator.state.records import (
    CardRecord,
    CardUpsertResult,
    ChunkRecord,
    DriveChannelRecord,
    RunReportRecord,
    SourceRecord,
    compute_card_content_hash,
    record_asdict_for_roundtrip,
)
def test_compute_card_content_hash_stable() -> None:
    h1 = compute_card_content_hash(
        simplified=" 词 ",
        traditional="詞",
        pinyin="cí",
        meaning="word",
        part_of_speech="noun",
        usage_notes="",
    )
    h2 = compute_card_content_hash(
        simplified=" 词 ",
        traditional="詞",
        pinyin="cí",
        meaning="word",
        part_of_speech="noun",
        usage_notes="",
    )
    assert h1 == h2
    assert len(h1) == 64


@pytest.mark.parametrize(
    "rec",
    [
        SourceRecord(source_id="s1", provider="local-filesystem", external_id="/x"),
        ChunkRecord(source_id="s1", chunk_index=0, chunk_sha256="abc"),
        CardRecord(card_id="c1", simplified="词"),
        DriveChannelRecord(channel_id="ch1"),
        RunReportRecord(run_id="r1", sync_report_json="{}"),
    ],
)
def test_record_json_roundtrip(rec: object) -> None:
    d = record_asdict_for_roundtrip(rec)
    json.dumps(d)


def test_card_upsert_result_enum_values() -> None:
    assert CardUpsertResult.CREATED == "created"


