from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_BASE_URL = "http://localhost:3001/api"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


@dataclass(frozen=True)
class Config:
    base_url: str
    api_key: str
    timeout: float
    host: str
    port: int
    workspace_slug: str | None


def load_config() -> Config:
    api_key = os.environ.get("ANYTHINGLLM_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "ANYTHINGLLM_API_KEY is not set. Set it to your AnythingLLM Developer API key."
        )

    base_url = os.environ.get("ANYTHINGLLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    timeout = float(os.environ.get("ANYTHINGLLM_TIMEOUT", "60"))
    host = os.environ.get("MCP_HOST", DEFAULT_HOST)
    port = int(os.environ.get("MCP_PORT", str(DEFAULT_PORT)))
    workspace_slug = os.environ.get("ANYTHINGLLM_WORKSPACE", "").strip() or None

    return Config(
        base_url=base_url,
        api_key=api_key,
        timeout=timeout,
        host=host,
        port=port,
        workspace_slug=workspace_slug,
    )
