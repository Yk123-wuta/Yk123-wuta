"""Async REST client for the Pet Hospital upstream service.

The upstream wraps every response in an envelope:
    {"code": 200, "message": "ok", "data": {...}, "time": "..."}

This client unwraps `data` so MCP tools can hand the payload to agents directly;
on failure it raises :class:`UpstreamError` with a machine readable error code,
a human readable message and (optionally) a sanitized upstream detail snippet.

Connection timeouts and HTTP 5xx responses are retried with exponential backoff
(both configurable through environment variables).
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx

from .config import config

DEFAULT_TIMEOUT = 10.0

# Machine readable error codes surfaced to MCP clients.
CODE_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
CODE_TIMEOUT = "UPSTREAM_TIMEOUT"
CODE_HTTP_ERROR = "UPSTREAM_HTTP_ERROR"
CODE_BUSINESS_ERROR = "UPSTREAM_BUSINESS_ERROR"
CODE_INVALID_RESPONSE = "UPSTREAM_INVALID_RESPONSE"

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")

logger = logging.getLogger(__name__)


def _safe_snippet(text: str, limit: int = 160) -> str | None:
    """Strip markup/control characters from upstream text before exposing it."""
    if not text:
        return None
    cleaned = _HTML_TAG_RE.sub(" ", text)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    return cleaned[:limit] or None


class UpstreamError(Exception):
    """Raised when the upstream REST service cannot be reached or returns an error."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        *,
        code: str = CODE_UNAVAILABLE,
        detail: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.retryable = retryable


class HospitalClient:
    """Thin async wrapper over the Pet Hospital REST API."""

    def __init__(
        self,
        base_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        timeout: float | None = None,
        max_retries: int | None = None,
        retry_backoff: float | None = None,
    ) -> None:
        self._base_url = (base_url or config.pet_hospital_url).rstrip("/")
        self._timeout = DEFAULT_TIMEOUT if timeout is None else timeout
        self._max_retries = (
            config.upstream_max_retries if max_retries is None else max_retries
        )
        self._retry_backoff = (
            config.upstream_retry_backoff
            if retry_backoff is None
            else retry_backoff
        )
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        """Release the underlying HTTP connection pool."""
        await self._client.aclose()

    async def __aenter__(self) -> HospitalClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def _sleep_before_retry(self, attempt: int) -> None:
        delay = max(0.0, self._retry_backoff) * (2 ** attempt)
        if delay:
            await asyncio.sleep(delay)

    def _log_retry(self, path: str, attempt: int, reason: str) -> None:
        logger.warning(
            "upstream retry path=%s attempt=%d/%d reason=%s",
            path,
            attempt + 1,
            self._max_retries,
            reason,
        )

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Perform a GET request and return the unwrapped `data` payload.

        Never raises for upstream business errors; it raises
        :class:`UpstreamError` when the service is unreachable, times out,
        returns 5xx (after retries) or replies with an error envelope.
        """
        attempts = max(0, self._max_retries) + 1

        for attempt in range(attempts):
            try:
                resp = await self._client.get(path, params=params)
            except httpx.TimeoutException as exc:
                if attempt < attempts - 1:
                    self._log_retry(path, attempt, type(exc).__name__)
                    await self._sleep_before_retry(attempt)
                    continue
                raise UpstreamError(
                    f"连接宠物医院 {self._base_url} 超时，请确认服务可用后重试",
                    code=CODE_TIMEOUT,
                    detail=_safe_snippet(str(exc)),
                    retryable=True,
                ) from exc
            except httpx.HTTPError as exc:
                raise UpstreamError(
                    f"无法连接宠物医院 {self._base_url}，请先启动 pethospital.exe",
                    code=CODE_UNAVAILABLE,
                    detail=_safe_snippet(str(exc)),
                ) from exc

            if 500 <= resp.status_code < 600:
                if attempt < attempts - 1:
                    self._log_retry(
                        path, attempt, f"HTTP {resp.status_code}"
                    )
                    await self._sleep_before_retry(attempt)
                    continue
                retries = attempts - 1
                message = (
                    f"上游返回 HTTP {resp.status_code}"
                    if retries == 0
                    else f"上游返回 HTTP {resp.status_code}，重试 {retries} 次后仍失败"
                )
                raise UpstreamError(
                    message,
                    status_code=resp.status_code,
                    code=CODE_HTTP_ERROR,
                    detail=_safe_snippet(resp.text),
                    retryable=retries > 0,
                )

            if resp.status_code != 200:
                raise UpstreamError(
                    f"上游返回 HTTP {resp.status_code}",
                    status_code=resp.status_code,
                    code=CODE_HTTP_ERROR,
                    detail=_safe_snippet(resp.text),
                )

            try:
                payload = resp.json()
            except ValueError as exc:
                raise UpstreamError(
                    "上游响应不是合法 JSON",
                    code=CODE_INVALID_RESPONSE,
                ) from exc

            code = payload.get("code")
            if code is None:
                # Endpoints without the standard envelope (e.g. /health).
                return payload

            if code != 200:
                safe_message = _safe_snippet(str(payload.get("message") or ""))
                message = f"上游业务错误 code={code}"
                if safe_message:
                    message = f"{message}: {safe_message}"
                raise UpstreamError(
                    message,
                    status_code=code,
                    code=CODE_BUSINESS_ERROR,
                    detail=safe_message,
                )

            return payload.get("data")

        # The loop always returns or raises; this line only satisfies type checkers.
        raise UpstreamError(
            "上游请求未完成",
            code=CODE_UNAVAILABLE,
        )

    async def fetch_all(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        page_size: int = 500,
        max_records: int = 20000,
    ) -> list[dict[str, Any]]:
        """Walk every upstream page and return the full list of records.

        Needed for filters the upstream does not support server-side
        (e.g. breed / gender / age). The upstream caps pageSize at 500.
        """
        base = dict(params or {})
        page = 1
        items: list[dict[str, Any]] = []
        while True:
            base["page"] = page
            base["pageSize"] = page_size
            payload = await self.get(path, params=base)
            chunk = payload.get("items", []) if isinstance(payload, dict) else []
            items.extend(chunk)
            total_pages = payload.get("totalPages") or 1
            if page >= total_pages or len(items) >= max_records:
                break
            page += 1
        return items[:max_records]
