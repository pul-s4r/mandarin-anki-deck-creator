"""Pure three-way merge for Anki note fields (pipeline vs user edits)."""

from __future__ import annotations

from dataclasses import dataclass

_CONFLICT_POLICIES = frozenset({"prefer-remote", "prefer-local", "tag-and-skip"})


@dataclass(frozen=True)
class MergeResult:
    merged_fields: dict[str, str]
    has_update: bool
    has_conflict: bool
    conflicted_field_names: list[str]


def three_way_merge(
    *,
    base_fields: dict[str, str] | None,
    remote_fields: dict[str, str],
    local_fields: dict[str, str],
    conflict_policy: str,
    field_names: tuple[str, ...],
) -> MergeResult:
    """Merge ``local`` against ``remote`` using optional ``base`` from last sync."""
    if conflict_policy not in _CONFLICT_POLICIES:
        raise ValueError(f"conflict_policy must be one of {sorted(_CONFLICT_POLICIES)}, got {conflict_policy!r}")

    if base_fields is None:
        merged = {k: local_fields.get(k, "") for k in field_names}
        has_update = any(merged.get(k, "") != remote_fields.get(k, "") for k in field_names)
        return MergeResult(
            merged_fields=merged,
            has_update=has_update,
            has_conflict=False,
            conflicted_field_names=[],
        )

    merged: dict[str, str] = {}
    conflict_names: list[str] = []

    for key in field_names:
        base_v = base_fields.get(key, "")
        remote_v = remote_fields.get(key, "")
        local_v = local_fields.get(key, "")
        base_same_remote = base_v == remote_v
        base_same_local = base_v == local_v

        if base_same_remote and base_same_local:
            merged[key] = remote_v
        elif base_same_remote and not base_same_local:
            merged[key] = local_v
        elif not base_same_remote and base_same_local:
            merged[key] = remote_v
        else:
            conflict_names.append(key)
            if conflict_policy == "prefer-remote":
                merged[key] = remote_v
            elif conflict_policy == "prefer-local":
                merged[key] = local_v
            else:
                merged[key] = remote_v

    if conflict_policy == "tag-and-skip" and conflict_names:
        merged_skip = {k: remote_fields.get(k, "") for k in field_names}
        return MergeResult(
            merged_fields=merged_skip,
            has_update=False,
            has_conflict=True,
            conflicted_field_names=list(conflict_names),
        )

    has_update = any(merged.get(k, "") != remote_fields.get(k, "") for k in field_names)
    return MergeResult(
        merged_fields=merged,
        has_update=has_update,
        has_conflict=bool(conflict_names),
        conflicted_field_names=list(conflict_names),
    )
