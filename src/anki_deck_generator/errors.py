"""Structured errors for the pipeline."""


class AnkiPipelineError(Exception):
    """Base class for recoverable pipeline failures."""


class IngestError(AnkiPipelineError):
    """Invalid or unsupported input (files, formats, dictionary text)."""


class LlmError(AnkiPipelineError):
    """LLM invocation, parsing, or fixture replay failures."""


class IntegrationError(AnkiPipelineError):
    """Third-party source or integration failures."""


class AuthenticationError(IntegrationError):
    """Authentication or authorization failures for an integration."""


class StateError(AnkiPipelineError):
    """SQLite / StateStore failures (schema mismatch, corrupt DB, etc.)."""

