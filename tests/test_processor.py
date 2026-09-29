"""Tests for processor parsing, idempotency, partial failures, and errors."""

import json
import uuid

import pytest
from botocore.exceptions import ClientError

from conftest import load_lambda_module


FILE_ID = "16fd2706-8baf-433b-82eb-8c7fada847da"


class FakeTable:
    def __init__(self, duplicate=False, error=None):
        self.duplicate = duplicate
        self.error = error
        self.calls = []

    def put_item(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        if self.duplicate:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}},
                "PutItem",
            )
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}


@pytest.fixture
def processor_module(monkeypatch):
    monkeypatch.setenv("METADATA_TABLE", "file-metadata")
    return load_lambda_module("processor_handler_test", "lambdas/processor/handler.py")


def s3_record(file_id=FILE_ID, key_name="data+file.csv"):
    return {
        "eventName": "ObjectCreated:Put",
        "eventTime": "2026-01-02T03:04:05.000Z",
        "s3": {
            "bucket": {"name": "upload-bucket"},
            "object": {"key": f"uploads/{file_id}/{key_name}", "size": 42},
        },
    }


def sqs_event(records, message_id="message-1"):
    return {
        "Records": [
            {
                "messageId": message_id,
                "body": json.dumps({"Records": records}),
            }
        ]
    }


def test_extracts_file_metadata(processor_module):
    metadata = processor_module.metadata_from_s3_record(
        s3_record(), processed_at="2026-01-02T03:05:00+00:00"
    )
    assert metadata == {
        "file_id": FILE_ID,
        "file_name": "data file.csv",
        "bucket": "upload-bucket",
        "object_key": f"uploads/{FILE_ID}/data file.csv",
        "size": 42,
        "status": "PROCESSED",
        "uploaded_at": "2026-01-02T03:04:05.000Z",
        "processed_at": "2026-01-02T03:05:00+00:00",
    }


def test_processes_and_conditionally_stores_metadata(processor_module, lambda_context):
    table = FakeTable()
    processor_module.TABLE = table
    result = processor_module.lambda_handler(sqs_event([s3_record()]), lambda_context)
    assert result == {"batchItemFailures": [], "processed": 1, "failed": 0}
    assert table.calls[0]["ConditionExpression"] == "attribute_not_exists(file_id)"
    assert table.calls[0]["Item"]["file_id"] == FILE_ID


def test_duplicate_event_is_successful_and_not_retried(processor_module, lambda_context):
    processor_module.TABLE = FakeTable(duplicate=True)
    result = processor_module.lambda_handler(sqs_event([s3_record()]), lambda_context)
    assert result == {"batchItemFailures": [], "processed": 0, "failed": 0}


@pytest.mark.parametrize(
    "invalid_record",
    [
        {},
        {"eventName": "ObjectRemoved:Delete"},
        s3_record(file_id="not-a-uuid"),
        {
            "eventName": "ObjectCreated:Put",
            "s3": {"bucket": {"name": "bucket"}, "object": {"key": "wrong/prefix.csv"}},
        },
    ],
)
def test_invalid_s3_events_return_partial_batch_failure(
    processor_module, lambda_context, invalid_record
):
    processor_module.TABLE = FakeTable()
    result = processor_module.lambda_handler(
        sqs_event([invalid_record], message_id="bad-message"), lambda_context
    )
    assert result["batchItemFailures"] == [{"itemIdentifier": "bad-message"}]
    assert result["processed"] == 0


def test_invalid_json_returns_partial_batch_failure(processor_module, lambda_context):
    event = {"Records": [{"messageId": "bad-json", "body": "{"}]}
    result = processor_module.lambda_handler(event, lambda_context)
    assert result["batchItemFailures"] == [{"itemIdentifier": "bad-json"}]


def test_dynamodb_error_returns_partial_batch_failure(processor_module, lambda_context):
    error = ClientError(
        {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "busy"}},
        "PutItem",
    )
    processor_module.TABLE = FakeTable(error=error)
    result = processor_module.lambda_handler(sqs_event([s3_record()]), lambda_context)
    assert result["batchItemFailures"] == [{"itemIdentifier": "message-1"}]
    assert result["failed"] == 1


def test_missing_records_rejected(processor_module, lambda_context):
    with pytest.raises(ValueError, match="Records list"):
        processor_module.lambda_handler({}, lambda_context)
