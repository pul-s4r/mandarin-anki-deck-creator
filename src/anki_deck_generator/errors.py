"""Structured errors for the pipeline (Epic A: base + ingest; expanded in A4)."""


class AnkiPipelineError(Exception):
    """Base class for recoverable pipeline failures."""


class IngestError(AnkiPipelineError):
    """Invalid or unsupported input (files, formats, dictionary text)."""

