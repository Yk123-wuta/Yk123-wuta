"""Async REST client for the Pet Hospital upstream service.

The upstream wraps every response in an envelope:
    {"code": 200, "message": "ok", "data": {...}, "time": "..."}

This client unwraps `data` so MCP tools can hand the payload to agents directly;
on failure it raises :class:`UpstreamError` with a human readable message.
"""

from __future__ import annotations

from typing import Any

import httpx

from .config import config

DEFAULT_TIMEOUT = 10.0


class UpstreamError(Exception):
    """Raised when the upstream REST service cannot be reached or returns an error."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class HospitalClient:
    """Thin async wrapper over the Pet Hospital REST API."""

    def __init__(
        self,
        base_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = (base_url or config.pet_hospital_url).rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=DEFAULT_TIMEOUT,
            transport=transport,
        )

    async def aclose(self) -> None:
        """Release the underlying HTTP connection pool."""
        await self._client.aclose()

    async def __aenter__(self) -> HospitalClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Perform a GET request and return the unwrapped `data` payload.

        Never raises for upstream business errors; it raises
        :class:`UpstreamError` when the service is unreachable or errors out.
        """
        try:
            resp = await self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise UpstreamError(
                f"无法连接宠物医院 {self._base_url}，请先启动 pethospital.exe（原因：{exc}）"
            ) from exc

        if resp.status_code != 200:
            raise UpstreamError(
                f"上游返回 HTTP {resp.status_code}: {resp.text[:200]}",
                status_code=resp.status_code,
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise UpstreamError("上游响应不是合法 JSON") from exc

        code = payload.get("code")
        if code is None:
            # Endpoints without the standard envelope (e.g. /health).
            return payload

        if code != 200:
            raise UpstreamError(
                f"上游业务错误 code={code}: {payload.get('message')}",
                status_code=code,
            )

        return payload.get("data")

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