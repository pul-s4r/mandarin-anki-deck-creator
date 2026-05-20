from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from anki_pipeline_core.config import LlmSettings, StateSettings, default_state_db_path

__all__ = ["Settings", "default_state_db_path"]


class Settings(LlmSettings, StateSettings):
    """Generator pipeline configuration (LLM, state, ingest, export)."""

    model_config = SettingsConfigDict(
        env_prefix="ANKI_PIPELINE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    chunk_size: int = 12000
    chunk_overlap: int = 400
    skip_lines_filter: bool = True
    csv_bom: bool = False
    cedict_force_overwrite: bool = False
    enable_decomposition_fallback: bool = True
    enable_llm_translation_fallback: bool = True

    input_path: Optional[Path] = None
    output_csv: Optional[Path] = None
    cedict_path: Optional[Path] = None

    enable_sentences: bool = True
    prior_csv: Optional[Path] = None
    sentence_links_csv: Optional[Path] = None
    sentence_assignment_strategy: str = "importance"  # "importance" | "random"
    sentence_random_seed: Optional[int] = None
    sentences_per_term: int = 1
    sentences_delimiter: str = " | "

    source_set_config: Optional[Path] = Field(
        default=None,
        description="YAML file defining source_sets (env: ANKI_PIPELINE_SOURCE_SET_CONFIG)",
    )
