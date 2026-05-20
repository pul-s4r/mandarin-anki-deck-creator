from __future__ import annotations

from typing import Protocol


class LlmClient(Protocol):
    """Minimal LLM invoke contract for content generation and extraction."""

    def invoke(self, system_prompt: str, user_prompt: str) -> str: ...
