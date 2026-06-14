"""DynamoDB single-table layout (IaC-agnostic definition)."""

from __future__ import annotations

from typing import Any

DEFAULT_TABLE_NAME = "anki-pipeline-state"
CARD_BY_KEY_INDEX = "card_by_key"
TTL_ATTRIBUTE = "ttl_unix"


def dynamo_table_definition(*, table_name: str = DEFAULT_TABLE_NAME) -> dict[str, Any]:
    """Return a ``create_table``-compatible definition for moto / CDK / SAM."""
    return {
        "TableName": table_name,
        "KeySchema": [
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
            {"AttributeName": "user_id", "AttributeType": "S"},
            {"AttributeName": "simplified", "AttributeType": "S"},
        ],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": CARD_BY_KEY_INDEX,
                "KeySchema": [
                    {"AttributeName": "user_id", "KeyType": "HASH"},
                    {"AttributeName": "simplified", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
        "BillingMode": "PAY_PER_REQUEST",
    }
