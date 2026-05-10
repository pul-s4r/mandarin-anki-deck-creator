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
class SourceSet:
    """Named collection of sources."""

    name: str
    sources: tuple[LocalFileSource, ...]


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
        sources: list[LocalFileSource] = []
        for i, s in enumerate(sources_raw):
            if not isinstance(s, dict):
                raise ValueError(f"source_sets.{name}.sources[{i}] must be a mapping")
            prov = s.get("provider")
            if prov != "local-filesystem":
                raise ValueError(f"Unsupported provider {prov!r} in {name}[{i}]")
            p = s.get("path")
            if not p:
                raise ValueError(f"Missing path in {name}[{i}]")
            pth = Path(str(p)).expanduser().resolve()
            ext = str(s.get("external_id") or str(pth))
            sources.append(LocalFileSource(provider="local-filesystem", path=pth, external_id=ext))
        out[str(name)] = SourceSet(name=str(name), sources=tuple(sources))
    return out


def pick_source_set(config: dict[str, SourceSet], name: str) -> SourceSet:
    if name not in config:
        raise KeyError(f"Unknown source set {name!r}; known: {sorted(config)}")
    return config[name]


def source_set_to_jsonable(config: dict[str, SourceSet]) -> dict[str, Any]:
    return {
        name: {
            "sources": [
                {"provider": s.provider, "path": str(s.path), "external_id": s.external_id} for s in ss.sources
            ]
        }
        for name, ss in config.items()
    }
