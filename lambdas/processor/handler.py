"""Lambda handler for processing S3 notifications delivered through SQS."""

import json
import os
from datetime import datetime, timezone
from urllib.parse import unquote_plus

import boto3


DYNAMODB = boto3.resource("dynamodb")
TABLE = DYNAMODB.Table(os.environ.get("METADATA_TABLE", "file-metadata"))


def _metadata_from_s3_record(record):
    bucket = record["s3"]["bucket"]["name"]
    object_data = record["s3"]["object"]
    object_key = unquote_plus(object_data["key"])
    key_parts = object_key.split("/", 2)
    if len(key_parts) != 3 or key_parts[0] != "uploads":
        raise ValueError("Unexpected S3 object key format.")

    uploaded_at = record.get("eventTime") or datetime.now(timezone.utc).isoformat()
    return {
        "file_id": key_parts[1],
        "file_name": key_parts[2],
        "bucket": bucket,
        "object_key": object_key,
        "size": int(object_data.get("size", 0)),
        "status": "PROCESSED",
        "uploaded_at": uploaded_at,
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }


def lambda_handler(event, context):
    """Process each SQS message and persist S3 object metadata."""
    processed = 0
    for sqs_record in event.get("Records", []):
        message = json.loads(sqs_record["body"])
        for s3_record in message.get("Records", []):
            metadata = _metadata_from_s3_record(s3_record)
            TABLE.put_item(Item=metadata)
            processed += 1
    return {"processed": processed}
