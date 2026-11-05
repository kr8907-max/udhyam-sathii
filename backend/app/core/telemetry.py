
from __future__ import annotations
import json
import logging
import re
import time
import uuid
from typing import Any, Optional
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

logger = logging.getLogger("udyam_saathi.telemetry")

SENSITIVE_KEY_PATTERNS = [
    re.compile(r"password", re.IGNORECASE),
    re.compile(r"token", re.IGNORECASE),
    re.compile(r"secret", re.IGNORECASE),
    re.compile(r"aadhaar", re.IGNORECASE),
    re.compile(r"pan", re.IGNORECASE),
    re.compile(r"phone", re.IGNORECASE),
    re.compile(r"mobile", re.IGNORECASE),
    re.compile(r"api[-_]?key", re.IGNORECASE),
]


def sanitize_value(key: str, val: Any) -> Any:
    if any(p.search(key) for p in SENSITIVE_KEY_PATTERNS):
        if isinstance(val, str) and len(val) > 4:
            return f"***{val[-4:]}"
        return "***REDACTED***"
    if isinstance(val, dict):
        return {k: sanitize_value(k, v) for k, v in val.items()}
    if isinstance(val, list):
        return [sanitize_value(key, item) for item in val]
    return val


def sanitize_dict(d: dict[str, Any]) -> dict[str, Any]:
    return {k: sanitize_value(k, v) for k, v in d.items()}


class TelemetryMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming_cid = request.headers.get("X-Correlation-ID") or request.headers.get("X-Request-ID")
        correlation_id = incoming_cid.strip() if incoming_cid else f"CID-{uuid.uuid4().hex[:12].upper()}"

        request.state.correlation_id = correlation_id
        request.state.request_id = correlation_id

        start_time = time.perf_counter()
        client_host = request.client.host if request.client else "unknown"

        sanitized_params = sanitize_dict(dict(request.query_params))
        logger.info(
            f"[{correlation_id}] ➡️ INCOMING {request.method} {request.url.path} "
            f"from {client_host} | params={sanitized_params}"
        )

        try:
            response = await call_next(request)
            duration_ms = (time.perf_counter() - start_time) * 1000.0

            response.headers["X-Correlation-ID"] = correlation_id
            response.headers["X-Request-ID"] = correlation_id
            response.headers["X-Response-Time-ms"] = f"{duration_ms:.2f}"

            logger.info(
                f"[{correlation_id}] ⬅️ RESPONSE {response.status_code} "
                f"{request.method} {request.url.path} in {duration_ms:.2f}ms"
            )
            return response

        except Exception as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            logger.error(
                f"[{correlation_id}] ❌ ERROR in {duration_ms:.2f}ms on {request.method} {request.url.path}: {exc}",
                exc_info=True,
            )
            raise exc
