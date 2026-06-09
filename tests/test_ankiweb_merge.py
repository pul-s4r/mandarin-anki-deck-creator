"""Tests for three-way field merge used by AnkiWeb export."""

from __future__ import annotations

import pytest

from anki_deck_generator.export.ankiweb.merge import three_way_merge

_FIELDS = ("Simplified", "Meaning")


def test_invalid_policy_raises() -> None:
    with pytest.raises(ValueError, match="conflict_policy"):
        three_way_merge(
            base_fields={},
            remote_fields={},
            local_fields={},
            conflict_policy="nope",
            field_names=_FIELDS,
        )


@pytest.mark.parametrize(
    ("base", "remote", "local", "expected_meaning", "expect_update"),
    [
        # same / same
        (
            {"Simplified": "学", "Meaning": "study"},
            {"Simplified": "学", "Meaning": "study"},
            {"Simplified": "学", "Meaning": "study"},
            "study",
            False,
        ),
        # same remote / different local -> take local
        (
            {"Simplified": "学", "Meaning": "study"},
            {"Simplified": "学", "Meaning": "study"},
            {"Simplified": "学", "Meaning": "learn"},
            "learn",
            True,
        ),
        # different remote / same local (user edited) -> keep remote
        (
            {"Simplified": "学", "Meaning": "study"},
            {"Simplified": "学", "Meaning": "user"},
            {"Simplified": "学", "Meaning": "study"},
            "user",
            False,
        ),
    ],
)
def test_merge_matrix_non_conflict_rows(
    base: dict[str, str],
    remote: dict[str, str],
    local: dict[str, str],
    expected_meaning: str,
    expect_update: bool,
) -> None:
    r = three_way_merge(
        base_fields=base,
        remote_fields=remote,
        local_fields=local,
        conflict_policy="prefer-remote",
        field_names=_FIELDS,
    )
    assert r.merged_fields["Meaning"] == expected_meaning
    assert r.has_update is expect_update
    assert r.has_conflict is False


def test_diff_diff_prefer_remote() -> None:
    r = three_way_merge(
        base_fields={"Simplified": "学", "Meaning": "base"},
        remote_fields={"Simplified": "学", "Meaning": "remote"},
        local_fields={"Simplified": "学", "Meaning": "local"},
        conflict_policy="prefer-remote",
        field_names=_FIELDS,
    )
    assert r.merged_fields["Meaning"] == "remote"
    assert r.has_conflict is True
    assert "Meaning" in r.conflicted_field_names


def test_diff_diff_prefer_local() -> None:
    r = three_way_merge(
        base_fields={"Simplified": "学", "Meaning": "base"},
        remote_fields={"Simplified": "学", "Meaning": "remote"},
        local_fields={"Simplified": "学", "Meaning": "local"},
        conflict_policy="prefer-local",
        field_names=_FIELDS,
    )
    assert r.merged_fields["Meaning"] == "local"
    assert r.has_conflict is True


def test_diff_diff_tag_and_skip() -> None:
    r = three_way_merge(
        base_fields={"Simplified": "学", "Meaning": "base"},
        remote_fields={"Simplified": "学", "Meaning": "remote"},
        local_fields={"Simplified": "学", "Meaning": "local"},
        conflict_policy="tag-and-skip",
        field_names=_FIELDS,
    )
    assert r.merged_fields["Meaning"] == "remote"
    assert r.has_update is False
    assert r.has_conflict is True


def test_base_none_pushes_local() -> None:
    r = three_way_merge(
        base_fields=None,
        remote_fields={"Simplified": "学", "Meaning": ""},
        local_fields={"Simplified": "学", "Meaning": "new"},
        conflict_policy="prefer-remote",
        field_names=_FIELDS,
    )
    assert r.merged_fields["Meaning"] == "new"
    assert r.has_update is True
    assert r.has_conflict is False


def test_idempotent_reapply_own_merge_output() -> None:
    base = {"Simplified": "学", "Meaning": "old"}
    remote = {"Simplified": "学", "Meaning": "user_edit"}
    local = {"Simplified": "学", "Meaning": "pipeline"}
    first = three_way_merge(
        base_fields=base,
        remote_fields=remote,
        local_fields=local,
        conflict_policy="prefer-remote",
        field_names=_FIELDS,
    )
    second = three_way_merge(
        base_fields=first.merged_fields,
        remote_fields=first.merged_fields,
        local_fields=first.merged_fields,
        conflict_policy="prefer-remote",
        field_names=_FIELDS,
    )
    assert second.has_update is False
    assert second.has_conflict is False
    assert second.merged_fields == first.merged_fields
