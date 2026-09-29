"""Tests for upload URL validation, responses, and AWS error handling."""

import base64
import json

import pytest

from conftest import load_lambda_module


class FakeS3Client:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def generate_presigned_url(self, operation, **kwargs):
        if self.error:
            raise self.error
        self.calls.append((operation, kwargs))
        return "https://uploads.example.test/signed"


@pytest.fixture
def upload_module(monkeypatch):
    monkeypatch.setenv("UPLOAD_BUCKET", "example-upload-bucket")
    monkeypatch.setenv("URL_EXPIRATION_SECONDS", "600")
    return load_lambda_module("upload_handler_test", "lambdas/upload_url/handler.py")


def api_event(body, base64_encoded=False):
    serialized = json.dumps(body)
    if base64_encoded:
        serialized = base64.b64encode(serialized.encode()).decode()
    return {"body": serialized, "isBase64Encoded": base64_encoded}


def response_body(response):
    return json.loads(response["body"])


def test_generates_presigned_url_for_valid_request(upload_module, lambda_context):
    fake_s3 = FakeS3Client()
    upload_module.S3_CLIENT = fake_s3

    response = upload_module.lambda_handler(
        api_event({"file_name": "daily-report.csv", "content_type": "text/csv"}),
        lambda_context,
    )

    body = response_body(response)
    assert response["statusCode"] == 201
    assert body["upload_url"] == "https://uploads.example.test/signed"
    assert body["object_key"].startswith(f"uploads/{body['file_id']}/")
    operation, call = fake_s3.calls[0]
    assert operation == "put_object"
    assert call["Params"]["Bucket"] == "example-upload-bucket"
    assert call["Params"]["ContentType"] == "text/csv"
    assert call["ExpiresIn"] == 600


@pytest.mark.parametrize(
    ("body", "expected_error"),
    [
        ({}, "file_name is required"),
        ({"file_name": "../secret.txt", "content_type": "text/plain"}, "plain file name"),
        ({"file_name": "image.png", "content_type": "image/png"}, "Unsupported file type"),
        ({"file_name": "data.json", "content_type": "text/plain"}, "content_type must be"),
    ],
)
def test_rejects_invalid_upload_requests(upload_module, lambda_context, body, expected_error):
    upload_module.S3_CLIENT = FakeS3Client()
    response = upload_module.lambda_handler(api_event(body), lambda_context)
    assert response["statusCode"] == 400
    assert expected_error in response_body(response)["error"]


def test_accepts_base64_encoded_api_body(upload_module, lambda_context):
    upload_module.S3_CLIENT = FakeS3Client()
    response = upload_module.lambda_handler(
        api_event({"file_name": "events.json", "content_type": "application/json"}, True),
        lambda_context,
    )
    assert response["statusCode"] == 201


def test_rejects_malformed_json(upload_module, lambda_context):
    response = upload_module.lambda_handler({"body": "not-json"}, lambda_context)
    assert response["statusCode"] == 400


def test_hides_internal_aws_errors(upload_module, lambda_context):
    upload_module.S3_CLIENT = FakeS3Client(RuntimeError("sensitive internal failure"))
    response = upload_module.lambda_handler(
        api_event({"file_name": "notes.txt", "content_type": "text/plain"}),
        lambda_context,
    )
    assert response["statusCode"] == 500
    assert "sensitive" not in response["body"]
    assert response_body(response)["request_id"] == "request-123"
