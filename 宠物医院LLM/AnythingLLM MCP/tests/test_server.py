from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from mcp import MCPError

import anythingllm_mcp.server as server


async def test_list_workspaces_tool():
    mock_client = AsyncMock()
    mock_client.list_workspaces.return_value = {"workspaces": [{"slug": "a"}]}
    with patch.object(server, "_client", return_value=mock_client):
        result = await server.list_workspaces()
    assert result == {"workspaces": [{"slug": "a"}]}


async def test_list_workspaces_tool_handles_missing_key():
    mock_client = AsyncMock()
    mock_client.list_workspaces.return_value = {}
    with patch.object(server, "_client", return_value=mock_client):
        result = await server.list_workspaces()
    assert result == {"workspaces": []}


async def test_get_workspace_tool_not_found():
    mock_client = AsyncMock()
    mock_client.get_workspace.return_value = {"workspace": []}
    with patch.object(server, "_client", return_value=mock_client):
        result = await server.get_workspace("missing")
    assert result == {"slug": "missing", "found": False, "workspace": None}


async def test_get_workspace_tool_found():
    mock_client = AsyncMock()
    mock_client.get_workspace.return_value = {"workspace": [{"slug": "a"}]}
    with patch.object(server, "_client", return_value=mock_client):
        result = await server.get_workspace("a")
    assert result == {"slug": "a", "found": True, "workspace": {"slug": "a"}}


async def test_create_workspace_tool_filters_none():
    mock_client = AsyncMock()
    mock_client.create_workspace.return_value = {"workspace": {"slug": "x"}, "message": "ok"}
    with patch.object(server, "_client", return_value=mock_client):
        result = await server.create_workspace(name="Test")
    mock_client.create_workspace.assert_awaited_once_with({"name": "Test"})
    assert result["workspace"] == {"slug": "x"}


async def test_create_workspace_tool_passes_extra_fields():
    mock_client = AsyncMock()
    mock_client.create_workspace.return_value = {"workspace": {"slug": "x"}}
    with patch.object(server, "_client", return_value=mock_client):
        await server.create_workspace(name="Test", topN=4, chatMode="query")
    mock_client.create_workspace.assert_awaited_once_with(
        {"name": "Test", "topN": 4, "chatMode": "query"}
    )


async def test_update_workspace_tool():
    mock_client = AsyncMock()
    mock_client.update_workspace.return_value = {"workspace": {"slug": "a"}}
    with patch.object(server, "_client", return_value=mock_client):
        await server.update_workspace("a", name="Renamed")
    mock_client.update_workspace.assert_awaited_once_with("a", {"name": "Renamed"})


async def test_delete_workspace_tool():
    mock_client = AsyncMock()
    mock_client.delete_workspace.return_value = None
    with patch.object(server, "_client", return_value=mock_client):
        result = await server.delete_workspace("a")
    mock_client.delete_workspace.assert_awaited_once_with("a")
    assert result == {"slug": "a", "deleted": True}


async def test_upload_document_tool_uploads_and_embeds():
    mock_client = AsyncMock()
    location = "custom-documents/faq.md-abc.json"
    mock_client.upload_document.return_value = {
        "success": True,
        "documents": [
            {"id": "doc-1", "title": "faq.md", "location": location}
        ],
    }
    mock_client.update_embeddings.return_value = {"message": "Workspace updated."}
    mock_client.get_workspace.return_value = {
        "workspace": [
            {"documents": [{"location": location, "vectorized": True}]}
        ]
    }
    with patch.object(server, "_client", return_value=mock_client):
        result = await server.upload_document(
            "faq.md", "hello", workspace="kb"
        )

    mock_client.upload_document.assert_awaited_once_with("faq.md", b"hello")
    mock_client.update_embeddings.assert_awaited_once_with("kb", adds=[location])
    assert result["workspace"] == "kb"
    assert result["uploaded_count"] == 1
    assert result["documents"] == [
        {
            "id": "doc-1",
            "title": "faq.md",
            "location": location,
            "vectorized": True,
        }
    ]
    assert result["embedding"] == "Workspace updated."


async def test_upload_document_tool_skips_embedding_when_disabled():
    mock_client = AsyncMock()
    mock_client.upload_document.return_value = {
        "success": True,
        "documents": [{"id": "doc-1", "title": "faq.md"}],
    }
    with patch.object(server, "_client", return_value=mock_client):
        result = await server.upload_document(
            "faq.md", "hello", add_to_workspace=False
        )

    mock_client.upload_document.assert_awaited_once_with("faq.md", b"hello")
    mock_client.update_embeddings.assert_not_awaited()
    assert result["workspace"] is None
    assert result["documents"][0]["vectorized"] is False


async def test_upload_document_tool_rejects_empty_content():
    with pytest.raises(MCPError):
        await server.upload_document("faq.md", "   ")


async def test_update_workspace_embeddings_tool():
    mock_client = AsyncMock()
    mock_client.update_embeddings.return_value = {"message": "Workspace updated."}
    mock_client.get_workspace.return_value = {
        "workspace": [
            {
                "documents": [
                    {
                        "id": "doc-1",
                        "title": "faq.md",
                        "location": "custom-documents/a.json",
                        "vectorized": True,
                    }
                ]
            }
        ]
    }
    with patch.object(server, "_client", return_value=mock_client):
        result = await server.update_workspace_embeddings(
            workspace="kb", adds=["custom-documents/a.json"]
        )

    mock_client.update_embeddings.assert_awaited_once_with(
        "kb", adds=["custom-documents/a.json"], deletes=None, reset=False
    )
    assert result["workspace"] == "kb"
    assert result["message"] == "Workspace updated."
    assert result["documents"][0]["vectorized"] is True


async def test_update_workspace_embeddings_tool_requires_action():
    with pytest.raises(MCPError):
        await server.update_workspace_embeddings(workspace="kb")
