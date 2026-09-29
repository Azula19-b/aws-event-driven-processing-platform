"""Lambda handler that returns a validated, short-lived S3 upload URL."""

import base64
import json
import os
import re
import uuid

import boto3


S3_CLIENT = boto3.client("s3")
BUCKET_NAME = os.environ.get("UPLOAD_BUCKET", "")
URL_EXPIRATION_SECONDS = int(os.environ.get("URL_EXPIRATION_SECONDS", "900"))
MAX_REQUEST_BODY_BYTES = 16_384
ALLOWED_CONTENT_TYPES = {
    ".csv": "text/csv",
    ".json": "application/json",
    ".txt": "text/plain",
}


def _response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def _request_body(event):
    raw_body = event.get("body")
    if raw_body is None:
        raise ValueError("Request body is required.")
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
    try:
        body = _request_body(event)
        file_name, content_type = _validated_upload(body)
        file_id = str(uuid.uuid4())
        object_key = f"uploads/{file_id}/{file_name}"
        upload_url = S3_CLIENT.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": BUCKET_NAME,
                "Key": object_key,
                "ContentType": content_type,
            },
            ExpiresIn=URL_EXPIRATION_SECONDS,
            HttpMethod="PUT",
        )
        return _response(
            201,
            {
                "file_id": file_id,
                "object_key": object_key,
                "upload_url": upload_url,
                "expires_in": URL_EXPIRATION_SECONDS,
            },
        )
    except (ValueError, TypeError, json.JSONDecodeError, base64.binascii.Error) as error:
        return _response(400, {"error": str(error)})
    except Exception:
        return _response(500, {"error": "Unable to generate an upload URL."})
