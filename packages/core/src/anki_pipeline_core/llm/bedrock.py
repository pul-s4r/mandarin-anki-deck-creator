from __future__ import annotations

from typing import Any

from langchain_aws import ChatBedrockConverse

from anki_pipeline_core.config import LlmSettings


def build_bedrock_converse(settings: LlmSettings) -> ChatBedrockConverse:
    """Construct a Bedrock chat model from shared LLM settings (no fixture handling)."""
    kwargs: dict[str, Any] = {
        "model_id": settings.bedrock_model_id,
        "temperature": settings.bedrock_temperature,
        "max_tokens": settings.bedrock_max_tokens,
    }
    if settings.aws_region:
        kwargs["region_name"] = settings.aws_region
    if settings.bedrock_top_p is not None:
        kwargs["top_p"] = settings.bedrock_top_p
    return ChatBedrockConverse(**kwargs)
