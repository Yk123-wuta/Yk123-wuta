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
        self.upstream_timeout = float(os.environ.get("PET_HOSPITAL_TIMEOUT", "10"))
        self.upstream_max_retries = int(
            os.environ.get("PET_HOSPITAL_MAX_RETRIES", "2")
        )
        self.upstream_retry_backoff = float(
            os.environ.get("PET_HOSPITAL_RETRY_BACKOFF", "0.15")
        )
        self.log_file = os.environ.get(
            "MCP_LOG_FILE", os.path.join("logs", "pet-hospital-mcp.log")
        )
        self.log_level = os.environ.get("MCP_LOG_LEVEL", "INFO").upper()


config = Config()
