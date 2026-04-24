from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def default_state_db_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    return Path(base).expanduser() / "anki-notes-pipeline" / "state.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ANKI_PIPELINE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    aws_region: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("ANKI_PIPELINE_AWS_REGION", "AWS_REGION"),
    )
    bedrock_model_id: str = Field(
        default="us.meta.llama4-scout-17b-instruct-v1:0",
        validation_alias=AliasChoices(
            "ANKI_PIPELINE_BEDROCK_MODEL_ID",
            "BEDROCK_MODEL_ID",
        ),
        description="Bedrock inference profile or model ID",
    )
    bedrock_temperature: float = 0.0
    bedrock_top_p: Optional[float] = None
    bedrock_top_k: Optional[int] = None
    bedrock_max_tokens: int = 8192

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

    llm_fixture_path: Optional[Path] = Field(
        default=None,
        description="If set, load LLM responses from this JSON file instead of calling Bedrock (env: ANKI_PIPELINE_LLM_FIXTURE_PATH)",
    )

    enable_sentences: bool = True
    prior_csv: Optional[Path] = None
    sentence_links_csv: Optional[Path] = None
    sentence_assignment_strategy: str = "importance"  # "importance" | "random"
    sentence_random_seed: Optional[int] = None
    sentences_per_term: int = 1
    sentences_delimiter: str = " | "

    state_backend: Literal["none", "sqlite"] = "none"
    state_db_path: Optional[Path] = None
    source_set_config: Optional[Path] = Field(
        default=None,
        description="YAML file defining source_sets (env: ANKI_PIPELINE_SOURCE_SET_CONFIG)",
    )
