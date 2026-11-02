from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx2
from mcp import MCPError

JSONRPC_INTERNAL_ERROR = -32603


class AnythingLLMError(MCPError):
    """Raised when the AnythingLLM API returns an error."""


class AnythingLLMClient:
    """Thin async wrapper around the AnythingLLM Developer API."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 60.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self._base_url}{path}"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            async with httpx2.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(method, url, headers=headers, **kwargs)
        except httpx2.HTTPError as exc:
            raise AnythingLLMError(
                JSONRPC_INTERNAL_ERROR,
                f"Failed to reach AnythingLLM at {url}: {exc}",
            ) from exc

        if response.status_code >= 400:
            detail = response.text.strip()
            raise AnythingLLMError(
                JSONRPC_INTERNAL_ERROR,
                f"AnythingLLM API error {response.status_code} for {method} {path}: {detail}",
            )

        if not response.content:
            return None

        try:
            return response.json()
        except ValueError:
            return response.text

    async def get(self, path: str, **kwargs: Any) -> Any:
        return await self._request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> Any:
        return await self._request("POST", path, **kwargs)

    async def delete(self, path: str, **kwargs: Any) -> Any:
        return await self._request("DELETE", path, **kwargs)

    async def list_workspaces(self) -> Any:
        return await self.get("/v1/workspaces")

    async def get_workspace(self, slug: str) -> Any:
        return await self.get(f"/v1/workspace/{quote(slug, safe='')}")

    async def create_workspace(self, payload: dict[str, Any]) -> Any:
        return await self.post("/v1/workspace/new", json=payload)

    async def update_workspace(self, slug: str, payload: dict[str, Any]) -> Any:
        return await self.post(
            f"/v1/workspace/{quote(slug, safe='')}/update", json=payload
        )

    async def delete_workspace(self, slug: str) -> Any:
        return await self.delete(f"/v1/workspace/{quote(slug, safe='')}")
