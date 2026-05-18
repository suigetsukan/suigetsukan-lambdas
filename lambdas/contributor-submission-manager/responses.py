"""
HTTP response builders. Standardize CORS headers and JSON encoding so route
handlers can return plain dicts/strings and a single helper layers on the
envelope expected by API Gateway's Lambda proxy integration.
"""

from __future__ import annotations

import json
import os
from typing import Any

from common.constants import (
    CORS_HEADERS_ALL,
    CORS_METHODS_GET_POST_OPTIONS,
    CORS_ORIGIN_ALL,
    HTTP_NO_CONTENT,
    HTTP_OK,
)


def cors_origin() -> str:
    """CORS origin from env; defaults to ``*`` for parity with siblings."""
    return os.environ.get("CORS_ALLOWED_ORIGIN", CORS_ORIGIN_ALL)


def cors_headers() -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": cors_origin(),
        "Access-Control-Allow-Headers": CORS_HEADERS_ALL,
        "Access-Control-Allow-Methods": CORS_METHODS_GET_POST_OPTIONS,
    }


def json_response(status_code: int, body: Any) -> dict:
    """Wrap a body in an API Gateway proxy response with CORS headers."""
    return {
        "statusCode": status_code,
        "headers": cors_headers(),
        "body": json.dumps(body),
    }


def success(body: Any) -> dict:
    return json_response(HTTP_OK, body)


def error(status_code: int, message: str) -> dict:
    return json_response(status_code, {"error": message})


def options() -> dict:
    return {
        "statusCode": HTTP_NO_CONTENT,
        "headers": cors_headers(),
        "body": "",
    }
