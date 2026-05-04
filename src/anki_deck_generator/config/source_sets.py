"""Versioned YAML configuration for named source sets (local filesystem first)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


@dataclass(frozen=True)
class LocalFileSource:
    """A single local file source."""

    provider: Literal["local-filesystem"]
    path: Path
    external_id: str


@dataclass(frozen=True)
class GoogleDriveSource:
    """Configured Google Drive folders and/or explicit file ids."""

    provider: Literal["google-drive"]
    folder_ids: tuple[str, ...]
    file_ids: tuple[str, ...]
    credentials_file: Path
    external_id: str


SourceEntry = LocalFileSource | GoogleDriveSource


@dataclass(frozen=True)
class SourceSet:
    """Named collection of sources."""

    name: str
    sources: tuple[SourceEntry, ...]


def _default_google_drive_external_id(folder_ids: tuple[str, ...], file_ids: tuple[str, ...]) -> str:
    fi = ",".join(sorted(folder_ids))
    ids = ",".join(sorted(file_ids))
    return f"google-drive:f:{fi}:i:{ids}"


def load_source_sets_yaml(path: Path) -> dict[str, SourceSet]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - exercised when extra missing
        raise ImportError(
            "PyYAML is required for source set configs. Install with: pip install 'anki-deck-generator[sync]'"
        ) from exc

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("source set YAML root must be a mapping")
    ver = int(raw.get("schema_version", 1))
    if ver != 1:
        raise ValueError(f"Unsupported schema_version: {ver}")

    sets_raw = raw.get("source_sets")
    if not isinstance(sets_raw, dict):
        raise ValueError("source_sets must be a mapping")

    out: dict[str, SourceSet] = {}
    for name, body in sets_raw.items():
        if not isinstance(body, dict):
            raise ValueError(f"source_sets.{name} must be a mapping")
        sources_raw = body.get("sources")
        if not isinstance(sources_raw, list):
            raise ValueError(f"source_sets.{name}.sources must be a list")
        sources: list[SourceEntry] = []
        for i, s in enumerate(sources_raw):
            if not isinstance(s, dict):
                raise ValueError(f"source_sets.{name}.sources[{i}] must be a mapping")
            prov = s.get("provider")
            if prov == "local-filesystem":
                p = s.get("path")
                if not p:
                    raise ValueError(f"Missing path in {name}[{i}]")
                pth = Path(str(p)).expanduser().resolve()
                ext = str(s.get("external_id") or str(pth))
                sources.append(LocalFileSource(provider="local-filesystem", path=pth, external_id=ext))
            elif prov == "google-drive":
                folders_raw = s.get("folder_ids") or []
                files_raw = s.get("file_ids") or []
                if not isinstance(folders_raw, list):
                    raise ValueError(f"source_sets.{name}.sources[{i}].folder_ids must be a list")
                if not isinstance(files_raw, list):
                    raise ValueError(f"source_sets.{name}.sources[{i}].file_ids must be a list")
                folder_ids = tuple(str(x) for x in folders_raw)
                file_ids = tuple(str(x) for x in files_raw)
                if not folder_ids and not file_ids:
                    raise ValueError(f"google-drive source {name}[{i}] needs folder_ids and/or file_ids")
                cf = s.get("credentials_file")
                if not cf:
                    raise ValueError(f"Missing credentials_file in {name}[{i}]")
                cred_path = Path(str(cf)).expanduser().resolve()
                ext = str(s.get("external_id") or _default_google_drive_external_id(folder_ids, file_ids))
                sources.append(
                    GoogleDriveSource(
                        provider="google-drive",
                        folder_ids=folder_ids,
                        file_ids=file_ids,
                        credentials_file=cred_path,
                        external_id=ext,
                    )
                )
            else:
                raise ValueError(f"Unsupported provider {prov!r} in {name}[{i}]")
        out[str(name)] = SourceSet(name=str(name), sources=tuple(sources))
    return out


def pick_source_set(config: dict[str, SourceSet], name: str) -> SourceSet:
    if name not in config:
        raise KeyError(f"Unknown source set {name!r}; known: {sorted(config)}")
    return config[name]


def source_set_to_jsonable(config: dict[str, SourceSet]) -> dict[str, Any]:
    def entry_json(s: SourceEntry) -> dict[str, Any]:
        if isinstance(s, LocalFileSource):
            return {"provider": s.provider, "path": str(s.path), "external_id": s.external_id}
        return {
            "provider": s.provider,
            "folder_ids": list(s.folder_ids),
            "file_ids": list(s.file_ids),
            "credentials_file": str(s.credentials_file),
            "external_id": s.external_id,
        }

    return {
        name: {"sources": [entry_json(s) for s in ss.sources]}
        for name, ss in config.items()
    }
