"""Exporter protocol for targets with a concrete output path (incremental sync)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from anki_deck_generator.pipeline_types import PipelineResult


@runtime_checkable
class FileTargetExporter(Protocol):
    """Serialize a pipeline result to bytes and expose where to write them on disk."""

    def export(self, result: PipelineResult) -> bytes: ...

    @property
    def filename_suggestion(self) -> str: ...

    @property
    def output_path(self) -> Path: ...
