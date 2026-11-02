from __future__ import annotations

from typing import Any

CREATE_WORKSPACE_FIELDS = (
    "similarityThreshold",
    "openAiTemp",
    "openAiHistory",
    "openAiPrompt",
    "queryRefusalResponse",
    "chatMode",
    "topN",
)

UPDATE_WORKSPACE_FIELDS = ("name",) + CREATE_WORKSPACE_FIELDS


def build_payload(**fields: Any) -> dict[str, Any]:
    """Build a JSON payload, dropping any field that is None."""
    return {key: value for key, value in fields.items() if value is not None}
