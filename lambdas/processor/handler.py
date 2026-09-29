"""Idempotent Lambda processor for S3 notifications delivered through SQS."""

import json
import os
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError


TABLE = None
TABLE_NAME = os.environ.get("METADATA_TABLE", "")


def _metadata_table():
    """Create the DynamoDB table resource lazily for reliable cold starts/tests."""
    global TABLE
    if TABLE is None:
        if not TABLE_NAME:
            raise RuntimeError("METADATA_TABLE is not configured.")
        TABLE = boto3.resource("dynamodb").Table(TABLE_NAME)
    return TABLE


def _request_id(context):
    return getattr(context, "aws_request_id", None) or "unknown"


def _log(request_id, event_type, status, **details):
    log_entry = {
        "request_id": request_id,
        "file_id": details.pop("file_id", None),
        "event_type": event_type,
        "status": status,
        "error": details.pop("error", None),
        **details,
    }
    print(json.dumps(log_entry, default=str, separators=(",", ":")))


def _metric(metric_name, value=1):
    print(
        json.dumps(
            {
                "_aws": {
                    "Timestamp": time.time_ns() // 1_000_000,
                    "CloudWatchMetrics": [
                        {
                            "Namespace": "EventProcessingPlatform",
                            "Dimensions": [["Service"]],
                            "Metrics": [{"Name": metric_name, "Unit": "Count"}],
                        }
                    ],
                },
                "Service": "Processor",
                metric_name: value,
            },
            separators=(",", ":"),
        )
    )


def metadata_from_s3_record(record, processed_at=None):
    """Validate an S3 event record and convert it into a DynamoDB item."""
    if not isinstance(record, dict) or not record.get("eventName", "").startswith("ObjectCreated:"):
        raise ValueError("Only S3 ObjectCreated events are supported.")

    try:
        bucket = record["s3"]["bucket"]["name"]
        object_data = record["s3"]["object"]
        object_key = unquote_plus(object_data["key"])
        key_parts = object_key.split("/", 2)
        if len(key_parts) != 3 or key_parts[0] != "uploads":
            raise ValueError("Unexpected S3 object key format.")
        uuid.UUID(key_parts[1])
        size = int(object_data.get("size", 0))
        if size < 0:
            raise ValueError("Object size cannot be negative.")
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Invalid S3 event: {error}") from error

    return {
        "file_id": key_parts[1],
        "file_name": key_parts[2],
        "bucket": bucket,
        "object_key": object_key,
        "size": size,
        "status": "PROCESSED",
        "uploaded_at": record.get("eventTime") or datetime.now(timezone.utc).isoformat(),
        "processed_at": processed_at or datetime.now(timezone.utc).isoformat(),
    }


def store_metadata(metadata):
    """Store once; return False when a prior delivery already created the item."""
    try:
        _metadata_table().put_item(
            Item=metadata,
            ConditionExpression="attribute_not_exists(file_id)",
        )
        return True
    except ClientError as error:
        error_code = error.response.get("Error", {}).get("Code")
        if error_code == "ConditionalCheckFailedException":
            return False
        raise


def _process_sqs_record(sqs_record, request_id):
    try:
        message = json.loads(sqs_record["body"])
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError(f"Invalid SQS message body: {error}") from error
    s3_records = message.get("Records")
    if not isinstance(s3_records, list) or not s3_records:
        raise ValueError("SQS message does not contain S3 records.")

    stored_count = 0
    for s3_record in s3_records:
        metadata = metadata_from_s3_record(s3_record)
        created = store_metadata(metadata)
        status = "processed" if created else "duplicate"
        event_type = "file.processed" if created else "file.duplicate"
        _log(request_id, event_type, status, file_id=metadata["file_id"])
        _metric("FilesProcessed" if created else "DuplicateEvents")
        stored_count += int(created)
    return stored_count


def lambda_handler(event, context):
    """Return SQS partial failures so only failed messages are retried."""
    records = event.get("Records")
    if not isinstance(records, list):
        raise ValueError("Event must contain an SQS Records list.")

    request_id = _request_id(context)
    failures = []
    processed = 0
    for sqs_record in records:
        message_id = sqs_record.get("messageId", "unknown")
        try:
            processed += _process_sqs_record(sqs_record, request_id)
        except Exception as error:
            _log(
                request_id,
                "file.processing_failed",
                "error",
                error=str(error),
                message_id=message_id,
            )
            _metric("ProcessingErrors")
            failures.append({"itemIdentifier": message_id})

    return {
        "batchItemFailures": failures,
        "processed": processed,
        "failed": len(failures),
    }
