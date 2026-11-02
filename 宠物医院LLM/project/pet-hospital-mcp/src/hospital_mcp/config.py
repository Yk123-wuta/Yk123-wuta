"""Runtime configuration for the MCP server.

Every value can be overridden through an environment variable.
"""

import os


class Config:
    """Resolved configuration (environment variables override defaults)."""

    def __init__(self) -> None:
        self.pet_hospital_url = os.environ.get(
            "PET_HOSPITAL_URL", "http://127.0.0.1:8080"
        ).rstrip("/")
        self.http_host = os.environ.get("MCP_HTTP_HOST", "127.0.0.1")
        self.http_port = int(os.environ.get("MCP_HTTP_PORT", "8301"))


config = Config()