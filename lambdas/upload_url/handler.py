"""Lambda handler that returns a validated, short-lived S3 upload URL."""

import base64
import binascii
import json
import os
import re
import time
import uuid

import boto3


S3_CLIENT = None
BUCKET_NAME = os.environ.get("UPLOAD_BUCKET", "")
URL_EXPIRATION_SECONDS = int(os.environ.get("URL_EXPIRATION_SECONDS", "900"))
MAX_REQUEST_BODY_BYTES = 16_384
ALLOWED_CONTENT_TYPES = {
    ".csv": "text/csv",
    ".json": "application/json",
    ".txt": "text/plain",
}


def _s3_client():
    """Create the AWS client lazily so imports and unit tests stay deterministic."""
    global S3_CLIENT
    if S3_CLIENT is None:
        S3_CLIENT = boto3.client("s3")
    return S3_CLIENT


def _response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "content-type": "application/json",
            "cache-control": "no-store",
        },
        "body": json.dumps(body),
    }


def _request_id(event, context):
    lambda_request_id = getattr(context, "aws_request_id", None)
    api_request_id = event.get("requestContext", {}).get("requestId")
    return lambda_request_id or api_request_id or "unknown"


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
                "Service": "UploadUrl",
                metric_name: value,
            },
            separators=(",", ":"),
        )
    )


def _request_body(event):
    raw_body = event.get("body")
    if raw_body is None:
        raise ValueError("Request body is required.")
    if not isinstance(raw_body, str):
        raise ValueError("Request body must be a JSON string.")
    if event.get("isBase64Encoded"):
        raw_body = base64.b64decode(raw_body, validate=True).decode("utf-8")
    if len(raw_body.encode("utf-8")) > MAX_REQUEST_BODY_BYTES:
        raise ValueError("Request body is too large.")
    body = json.loads(raw_body)
    if not isinstance(body, dict):
        raise ValueError("Request body must be a JSON object.")
    return body


def _validated_upload(body):
    file_name = body.get("file_name")
    content_type = body.get("content_type")
    if not isinstance(file_name, str) or not file_name.strip():
        raise ValueError("file_name is required.")
    if len(file_name) > 255 or "/" in file_name or "\\" in file_name:
        raise ValueError("file_name must be a plain file name up to 255 characters.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]*", file_name):
        raise ValueError("file_name contains unsupported characters.")

    extension = os.path.splitext(file_name)[1].lower()
    expected_content_type = ALLOWED_CONTENT_TYPES.get(extension)
    if expected_content_type is None:
        raise ValueError("Unsupported file type. Allowed types: csv, json, txt.")
    if content_type != expected_content_type:
        raise ValueError(f"content_type must be {expected_content_type} for {extension} files.")
    return file_name, content_type


def lambda_handler(event, context):
    """Validate an API request and return an S3 PUT presigned URL."""
    request_id = _request_id(event, context)
    try:
        if not BUCKET_NAME:
            raise RuntimeError("UPLOAD_BUCKET is not configured.")
        body = _request_body(event)
        file_name, content_type = _validated_upload(body)
        file_id = str(uuid.uuid4())
        object_key = f"uploads/{file_id}/{file_name}"
        upload_url = _s3_client().generate_presigned_url(
            "put_object",
            Params={
                "Bucket": BUCKET_NAME,
                "Key": object_key,
                "ContentType": content_type,
            },
            ExpiresIn=URL_EXPIRATION_SECONDS,
            HttpMethod="PUT",
        )
        _log(request_id, "upload_url.generated", "success", file_id=file_id)
        _metric("UploadUrlsGenerated")
        return _response(
            201,
            {
                "file_id": file_id,
                "object_key": object_key,
                "upload_url": upload_url,
                "expires_in": URL_EXPIRATION_SECONDS,
            },
        )
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError, binascii.Error) as error:
        _log(request_id, "upload_url.rejected", "invalid", error=str(error))
        _metric("UploadUrlValidationErrors")
        return _response(400, {"error": str(error), "request_id": request_id})
    except Exception as error:
        _log(request_id, "upload_url.failed", "error", error=str(error))
        _metric("UploadUrlErrors")
        return _response(
            500,
            {"error": "Unable to generate an upload URL.", "request_id": request_id},
        )
