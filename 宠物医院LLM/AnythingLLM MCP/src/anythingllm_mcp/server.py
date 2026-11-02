from __future__ import annotations

import logging
from typing import Any

from mcp import MCPError
from mcp.server.mcpserver import MCPServer

from .client import AnythingLLMClient
from .config import load_config
from .models import build_payload

mcp = MCPServer(
    "anythingllm",
    title="AnythingLLM",
    description="Answer questions from an AnythingLLM workspace knowledge base.",
    version="0.2.0",
)


async def _resolve_workspace(client: AnythingLLMClient, configured: str | None) -> str:
    if configured:
        return configured

    data = await client.get("/v1/workspaces")
    workspaces = data.get("workspaces", []) if isinstance(data, dict) else []
    if not workspaces:
        raise MCPError(-32603, "No workspace found in AnythingLLM.")
    return workspaces[0]["slug"]


def _client() -> AnythingLLMClient:
    config = load_config()
    return AnythingLLMClient(config.base_url, config.api_key, config.timeout)


@mcp.tool()
async def list_workspaces() -> dict[str, Any]:
    """List all workspaces in the AnythingLLM instance.

    Returns the list of workspaces with their basic metadata (id, name, slug,
    chat settings, and threads).
    """
    data = await _client().list_workspaces()
    workspaces = data.get("workspaces", []) if isinstance(data, dict) else []
    return {"workspaces": workspaces}


@mcp.tool()
async def get_workspace(slug: str) -> dict[str, Any]:
    """Get a workspace by its unique slug.

    Args:
        slug: Unique slug of the workspace to retrieve.
    """
    data = await _client().get_workspace(slug)
    workspaces = data.get("workspace", []) if isinstance(data, dict) else []
    if not workspaces:
        return {"slug": slug, "found": False, "workspace": None}
    return {"slug": slug, "found": True, "workspace": workspaces[0]}


@mcp.tool()
async def create_workspace(
    name: str,
    similarityThreshold: float | None = None,
    openAiTemp: float | None = None,
    openAiHistory: int | None = None,
    openAiPrompt: str | None = None,
    queryRefusalResponse: str | None = None,
    chatMode: str | None = None,
    topN: int | None = None,
) -> dict[str, Any]:
    """Create a new workspace.

    Args:
        name: Display name of the workspace (required).
        similarityThreshold: Minimum similarity score (0.0-1.0) for citations.
        openAiTemp: LLM temperature for this workspace.
        openAiHistory: Number of chat messages to recall in chat mode.
        openAiPrompt: Custom system prompt for responses.
        queryRefusalResponse: Response when no relevant sources are found.
        chatMode: Default chat mode (automatic, query, or chat).
        topN: Number of document chunks to retrieve per query.
    """
    payload = build_payload(
        similarityThreshold=similarityThreshold,
        openAiTemp=openAiTemp,
        openAiHistory=openAiHistory,
        openAiPrompt=openAiPrompt,
        queryRefusalResponse=queryRefusalResponse,
        chatMode=chatMode,
        topN=topN,
    )
    data = await _client().create_workspace({"name": name, **payload})
    if not isinstance(data, dict):
        return {"workspace": None, "message": str(data)}
    return {"workspace": data.get("workspace"), "message": data.get("message")}


@mcp.tool()
async def update_workspace(
    slug: str,
    name: str | None = None,
    similarityThreshold: float | None = None,
    openAiTemp: float | None = None,
    openAiHistory: int | None = None,
    openAiPrompt: str | None = None,
    queryRefusalResponse: str | None = None,
    chatMode: str | None = None,
    topN: int | None = None,
) -> dict[str, Any]:
    """Update an existing workspace's settings by its unique slug.

    Only the provided fields are updated; omitted fields are left unchanged.

    Args:
        slug: Unique slug of the workspace to update.
        name: New display name for the workspace.
        similarityThreshold: Minimum similarity score (0.0-1.0) for citations.
        openAiTemp: LLM temperature for this workspace.
        openAiHistory: Number of chat messages to recall in chat mode.
        openAiPrompt: Custom system prompt for responses.
        queryRefusalResponse: Response when no relevant sources are found.
        chatMode: Default chat mode (automatic, query, or chat).
        topN: Number of document chunks to retrieve per query.
    """
    payload = build_payload(
        name=name,
        similarityThreshold=similarityThreshold,
        openAiTemp=openAiTemp,
        openAiHistory=openAiHistory,
        openAiPrompt=openAiPrompt,
        queryRefusalResponse=queryRefusalResponse,
        chatMode=chatMode,
        topN=topN,
    )
    data = await _client().update_workspace(slug, payload)
    if not isinstance(data, dict):
        return {"workspace": None, "message": str(data)}
    return {"workspace": data.get("workspace"), "message": data.get("message")}


@mcp.tool()
async def delete_workspace(slug: str) -> dict[str, Any]:
    """Delete a workspace by its unique slug.

    Args:
        slug: Unique slug of the workspace to delete.
    """
    await _client().delete_workspace(slug)
    return {"slug": slug, "deleted": True}


@mcp.tool()
async def ask(question: str) -> dict[str, Any]:
    """Answer a question using the knowledge base of the AnythingLLM workspace.

    Args:
        question: The question to answer from the workspace documents.
    """
    config = load_config()
    client = AnythingLLMClient(config.base_url, config.api_key, config.timeout)
    slug = await _resolve_workspace(client, config.workspace_slug)

    data = await client.post(
        f"/v1/workspace/{slug}/chat",
        json={"message": question, "mode": "query"},
    )

    if not isinstance(data, dict):
        return {"workspace": slug, "answer": str(data), "sources": []}

    return {
        "workspace": slug,
        "answer": data.get("textResponse"),
        "sources": [
            {
                "title": source.get("title"),
                "text": source.get("text"),
                "score": source.get("score"),
            }
            for source in (data.get("sources") or [])
        ],
        "error": data.get("error"),
    }


def main() -> None:
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    logging.getLogger("httpcore2").setLevel(logging.WARNING)
    config = load_config()
    mcp.run(transport="streamable-http", host=config.host, port=config.port)
