from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from anythingllm_mcp.client import AnythingLLMClient, AnythingLLMError


def _mock_http(payload=None, status_code=200) -> AsyncMock:
    http = AsyncMock()
    response = MagicMock()
    response.status_code = status_code
    response.text = "error detail"
    if payload is None:
        response.content = b""
        response.json.side_effect = ValueError("not json")
    else:
        response.content = b"{}"
        response.json.return_value = payload
    http.request.return_value = response
    return http


def _patch_client(http: AsyncMock):
    mock_cls = MagicMock()

    async def aenter(_self):
        return http

    async def aexit(_self, *args):
        return None

    mock_cls.return_value.__aenter__ = aenter
    mock_cls.return_value.__aexit__ = aexit
    return patch("anythingllm_mcp.client.httpx2.AsyncClient", mock_cls)


async def test_list_workspaces():
    http = _mock_http({"workspaces": [{"slug": "a"}]})
    with _patch_client(http):
        result = await AnythingLLMClient("http://localhost:3001/api", "key").list_workspaces()
    assert result == {"workspaces": [{"slug": "a"}]}
    method, url = http.request.await_args.args[:2]
    assert method == "GET"
    assert url == "http://localhost:3001/api/v1/workspaces"


async def test_get_workspace_encodes_slug():
    http = _mock_http({"workspace": [{"slug": "my ws"}]})
    with _patch_client(http):
        await AnythingLLMClient("http://x/api", "key").get_workspace("my ws")
    method, url = http.request.await_args.args[:2]
    assert method == "GET"
    assert url == "http://x/api/v1/workspace/my%20ws"


async def test_create_workspace():
    http = _mock_http({"workspace": {"slug": "x"}, "message": "created"})
    with _patch_client(http):
        await AnythingLLMClient("http://x/api", "key").create_workspace({"name": "Test"})
    method, url = http.request.await_args.args[:2]
    assert method == "POST"
    assert url == "http://x/api/v1/workspace/new"
    assert http.request.await_args.kwargs["json"] == {"name": "Test"}


async def test_update_workspace():
    http = _mock_http({"workspace": {"slug": "x"}})
    with _patch_client(http):
        await AnythingLLMClient("http://x/api", "key").update_workspace("my ws", {"name": "New"})
    method, url = http.request.await_args.args[:2]
    assert method == "POST"
    assert url == "http://x/api/v1/workspace/my%20ws/update"


async def test_delete_workspace():
    http = _mock_http(None)
    with _patch_client(http):
        result = await AnythingLLMClient("http://x/api", "key").delete_workspace("my ws")
    assert result is None
    method, url = http.request.await_args.args[:2]
    assert method == "DELETE"
    assert url == "http://x/api/v1/workspace/my%20ws"


async def test_request_raises_on_error_status():
    http = _mock_http(None, status_code=400)
    with _patch_client(http):
        with pytest.raises(AnythingLLMError):
            await AnythingLLMClient("http://x/api", "key").list_workspaces()


async def test_request_returns_text_on_non_json():
    http = AsyncMock()
    response = MagicMock()
    response.status_code = 200
    response.content = b"plain text"
    response.text = "plain text"
    response.json.side_effect = ValueError("not json")
    http.request.return_value = response
    with _patch_client(http):
        result = await AnythingLLMClient("http://x/api", "key").list_workspaces()
    assert result == "plain text"
