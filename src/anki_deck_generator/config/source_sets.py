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
class EditSettlingConfig:
    """Debounce configuration for Drive push-notification edit settling.

    ``enabled`` defaults to True for Drive source sets.
    ``quiet_minutes``: how long to wait after the last edit before processing.
    ``max_delay_minutes``: hard ceiling; process even if edits keep arriving.
    """

    enabled: bool = True
    quiet_minutes: int = 10
    max_delay_minutes: int = 120

    @property
    def quiet_seconds(self) -> int:
        return self.quiet_minutes * 60

    @property
    def max_delay_seconds(self) -> int:
        return self.max_delay_minutes * 60


@dataclass(frozen=True)
class CsvExporterConfig:
    """CSV file export target from a source set."""

    type: Literal["csv"]
    destination: Path


@dataclass(frozen=True)
class XlsxExporterConfig:
    """XLSX file export target from a source set."""

    type: Literal["xlsx"]
    destination: Path


@dataclass(frozen=True)
class AnkiExporterConfig:
    """Direct desktop Anki export via AnkiConnect (local delivery mode)."""

    type: Literal["anki"]
    deck_name: str
    model_name: str = "Chinese vocabulary"
    anki_connect_url: str = "http://127.0.0.1:8765"
    anki_connect_api_key: str | None = None
    conflict_policy: str = "prefer-remote"
    auto_create_deck: bool = True
    auto_create_model: bool = True
    auto_sync: bool = True


ExporterConfig = CsvExporterConfig | XlsxExporterConfig | AnkiExporterConfig


@dataclass(frozen=True)
class SourceSet:
    """Named collection of sources and optional export targets."""

    name: str
    sources: tuple[SourceEntry, ...]
    exporters: tuple[ExporterConfig, ...] = ()
    edit_settling: EditSettlingConfig = EditSettlingConfig()


def _default_google_drive_external_id(folder_ids: tuple[str, ...], file_ids: tuple[str, ...]) -> str:
    fi = ",".join(sorted(folder_ids))
    ids = ",".join(sorted(file_ids))
    return f"google-drive:f:{fi}:i:{ids}"


def _parse_exporter_config(name: str, raw: object) -> ExporterConfig:
    if not isinstance(raw, dict):
        raise ValueError(f"source_sets.{name}.exporters entries must be mappings")
    exp_type = raw.get("type")
    if exp_type == "anki":
        deck_name = raw.get("deck_name")
        if not deck_name:
            raise ValueError(f"Missing deck_name in source_sets.{name} anki exporter {raw!r}")
        api_key = raw.get("anki_connect_api_key")
        return AnkiExporterConfig(
            type="anki",
            deck_name=str(deck_name),
            model_name=str(raw.get("model_name", "Chinese vocabulary")),
            anki_connect_url=str(raw.get("anki_connect_url", "http://127.0.0.1:8765")),
            anki_connect_api_key=str(api_key) if api_key else None,
            conflict_policy=str(raw.get("conflict_policy", "prefer-remote")),
            auto_create_deck=bool(raw.get("auto_create_deck", True)),
            auto_create_model=bool(raw.get("auto_create_model", True)),
            auto_sync=bool(raw.get("auto_sync", True)),
        )
    dest = raw.get("destination")
    if not dest:
        raise ValueError(f"Missing destination in source_sets.{name} exporter {raw!r}")
    path = Path(str(dest)).expanduser()
    if exp_type == "csv":
        return CsvExporterConfig(type="csv", destination=path)
    if exp_type == "xlsx":
        return XlsxExporterConfig(type="xlsx", destination=path)
    raise ValueError(f"Unsupported exporter type {exp_type!r} in source_sets.{name}")


def _exporter_json(cfg: ExporterConfig) -> dict[str, Any]:
    if isinstance(cfg, AnkiExporterConfig):
        out: dict[str, Any] = {
            "type": cfg.type,
            "deck_name": cfg.deck_name,
            "model_name": cfg.model_name,
            "anki_connect_url": cfg.anki_connect_url,
            "conflict_policy": cfg.conflict_policy,
            "auto_create_deck": cfg.auto_create_deck,
            "auto_create_model": cfg.auto_create_model,
            "auto_sync": cfg.auto_sync,
        }
        if cfg.anki_connect_api_key:
            out["anki_connect_api_key"] = cfg.anki_connect_api_key
        return out
    return {"type": cfg.type, "destination": str(cfg.destination)}


def _parse_edit_settling(name: str, body: dict[str, Any]) -> EditSettlingConfig:
    raw = body.get("edit_settling")
    if raw is None:
        return EditSettlingConfig()
    if not isinstance(raw, dict):
        raise ValueError(f"source_sets.{name}.edit_settling must be a mapping")
    enabled = bool(raw.get("enabled", True))
    quiet_minutes = int(raw.get("quiet_minutes", 10))
    max_delay_minutes = int(raw.get("max_delay_minutes", 120))
    return EditSettlingConfig(
        enabled=enabled,
        quiet_minutes=quiet_minutes,
        max_delay_minutes=max_delay_minutes,
    )


def _parse_exporters(name: str, body: dict[str, Any]) -> tuple[ExporterConfig, ...]:
    exporters_raw = body.get("exporters")
    if exporters_raw is None:
        return ()
    if not isinstance(exporters_raw, list):
        raise ValueError(f"source_sets.{name}.exporters must be a list")
    return tuple(_parse_exporter_config(name, item) for item in exporters_raw)


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
        exporters = _parse_exporters(name, body)
        edit_settling = _parse_edit_settling(name, body)
        out[str(name)] = SourceSet(
            name=str(name),
            sources=tuple(sources),
            exporters=exporters,
            edit_settling=edit_settling,
        )
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
        name: {
            "sources": [entry_json(s) for s in ss.sources],
            "exporters": [_exporter_json(e) for e in ss.exporters],
            "edit_settling": {
                "enabled": ss.edit_settling.enabled,
                "quiet_minutes": ss.edit_settling.quiet_minutes,
                "max_delay_minutes": ss.edit_settling.max_delay_minutes,
            },
        }
        for name, ss in config.items()
    }
