"""Lambda initialization helpers for serverless deployment.

Handles cold-start resource provisioning:
- Fetch Drive OAuth token from Secrets Manager
- Download source config YAML from S3
- Ensure CEDICT dictionary is available
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def init_drive_credentials() -> Path:
    """Fetch Drive OAuth token from Secrets Manager and write to /tmp."""
    secret_arn = os.environ.get("DRIVE_CREDENTIALS_SECRET_ARN")
    credentials_path = os.environ.get("GOOGLE_DRIVE_CREDENTIALS_FILE", "/tmp/google-drive-token.json")

    if not secret_arn:
        logger.warning("DRIVE_CREDENTIALS_SECRET_ARN not set, skipping secret fetch")
        return Path(credentials_path)

    credentials_file = Path(credentials_path)
    if credentials_file.exists():
        return credentials_file

    try:
        import boto3

        client = boto3.client("secretsmanager")
        response = client.get_secret_value(SecretId=secret_arn)
        secret_value = response["SecretString"]

        credentials_file.parent.mkdir(parents=True, exist_ok=True)
        credentials_file.write_text(secret_value)
        logger.info("Drive credentials written to %s", credentials_file)
    except Exception as exc:
        logger.error("Failed to fetch Drive credentials: %s", exc)
        raise

    return credentials_file


def init_source_config() -> Path:
    """Download source config YAML from S3 to /tmp."""
    config_bucket = os.environ.get("SOURCE_CONFIG_BUCKET")
    config_key = os.environ.get("SOURCE_CONFIG_KEY", "sources.yaml")
    config_path = os.environ.get("ANKI_PIPELINE_SOURCE_SET_CONFIG", "/tmp/sources.yaml")

    if not config_bucket:
        logger.warning("SOURCE_CONFIG_BUCKET not set, skipping config download")
        return Path(config_path)

    config_file = Path(config_path)
    if config_file.exists():
        return config_file

    try:
        import boto3

        s3 = boto3.client("s3")
        config_file.parent.mkdir(parents=True, exist_ok=True)
        s3.download_file(config_bucket, config_key, str(config_file))
        logger.info("Source config downloaded to %s", config_file)
    except Exception as exc:
        logger.error("Failed to download source config: %s", exc)
        raise

    return config_file


def init_cedict() -> Path:
    """Ensure CEDICT dictionary is available at the configured path."""
    cedict_path = os.environ.get("ANKI_PIPELINE_CEDICT_PATH", "/opt/cedict/cedict_ts.u8")
    cedict_file = Path(cedict_path)

    if cedict_file.exists():
        return cedict_file

    cedict_bucket = os.environ.get("CEDICT_BUCKET")
    cedict_key = os.environ.get("CEDICT_KEY", "cedict_ts.u8")

    if not cedict_bucket:
        logger.warning("CEDICT not found at %s and CEDICT_BUCKET not set", cedict_path)
        return cedict_file

    try:
        import boto3

        s3 = boto3.client("s3")
        cedict_file.parent.mkdir(parents=True, exist_ok=True)
        s3.download_file(cedict_bucket, cedict_key, str(cedict_file))
        logger.info("CEDICT downloaded to %s", cedict_file)
    except Exception as exc:
        logger.error("Failed to download CEDICT: %s", exc)
        raise

    return cedict_file


def init_all() -> None:
    """Initialize all Lambda resources at cold start."""
    init_drive_credentials()
    init_source_config()
    init_cedict()
