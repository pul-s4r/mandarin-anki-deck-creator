from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LlmSettings(BaseSettings):
    """AWS Bedrock and LLM fixture configuration shared across consumers."""

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
    llm_fixture_path: Optional[Path] = Field(
        default=None,
        description="If set, load LLM responses from this JSON file instead of calling Bedrock",
    )
