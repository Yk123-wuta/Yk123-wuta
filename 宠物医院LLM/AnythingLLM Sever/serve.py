"""AnythingLLM 上传页面本地代理。

浏览器只访问本服务（默认 http://127.0.0.1:3002），由本服务携带
ANYTHINGLLM_API_KEY 请求 AnythingLLM 开发者 API，密钥不会出现在前端代码中。

环境变量：
  ANYTHINGLLM_API_KEY   必需，AnythingLLM Developer API Key
  ANYTHINGLLM_BASE_URL  可选，默认 http://localhost:3001/api
  ANYTHINGLLM_TIMEOUT   可选，请求上游超时秒数，默认 60
  WEB_HOST              可选，本服务监听地址，默认 127.0.0.1
  WEB_PORT              可选，本服务监听端口，默认 3002
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent
INDEX_FILE = ROOT / "upload.html"

BASE_URL = os.environ.get("ANYTHINGLLM_BASE_URL", "http://localhost:3001/api").rstrip("/")
API_KEY = os.environ.get("ANYTHINGLLM_API_KEY", "").strip()
TIMEOUT = float(os.environ.get("ANYTHINGLLM_TIMEOUT", "60"))
HOST = os.environ.get("WEB_HOST", "127.0.0.1")
PORT = int(os.environ.get("WEB_PORT", "3002"))

MAX_UPLOAD_BYTES = 64 * 1024 * 1024

logger = logging.getLogger("anythingllm-proxy")


def _forward(method: str, url: str, body: bytes | None, content_type: str | None) -> tuple[int, str, bytes]:
    """向 AnythingLLM 发起请求，返回 (状态码, Content-Type, 响应体)。"""
    headers = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"}
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, response.headers.get("Content-Type", "application/json"), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", "application/json"), exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ConnectionError(f"无法连接 AnythingLLM（{url}）：{exc}") from exc


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "AnythingLLMProxy/1.0"

    def log_message(self, fmt: str, *args) -> None:  # 统一走 logging
        logger.info("%s - %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path)
        if path in ("/", "/upload.html", "/index.html"):
            self._send_file()
            return
        if path == "/api/workspaces":
            self._proxy("GET", "/v1/workspaces")
            return
        self._send_json(404, {"success": False, "error": "接口不存在"})

    def do_POST(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path)
        if path == "/api/upload":
            body = self._read_body()
            if body is None:
                return
            content_type = self.headers.get("Content-Type")
            self._proxy("POST", "/v1/document/upload", body, content_type)
            return
        self._send_json(404, {"success": False, "error": "接口不存在"})

    def _read_body(self) -> bytes | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"success": False, "error": "Content-Length 无效"})
            return None
        if length <= 0:
            self._send_json(400, {"success": False, "error": "请求体为空"})
            return None
        if length > MAX_UPLOAD_BYTES:
            self._send_json(413, {"success": False, "error": "文件超过 64MB 限制"})
            return None
        return self.rfile.read(length)

    def _proxy(self, method: str, upstream_path: str, body: bytes | None = None,
               content_type: str | None = None) -> None:
        if not API_KEY:
            self._send_json(
                503,
                {"success": False,
                 "error": "服务端未配置 ANYTHINGLLM_API_KEY，请设置环境变量后重启本服务"},
            )
            return
        try:
            status, resp_type, payload = _forward(
                method, f"{BASE_URL}{upstream_path}", body, content_type
            )
        except ConnectionError as exc:
            logger.error("上游不可达：%s", exc)
            self._send_json(502, {"success": False, "error": str(exc)})
            return
        logger.info("转发 %s %s -> %s", method, upstream_path, status)
        self._send_bytes(status, resp_type, payload)

    def _send_file(self) -> None:
        try:
            data = INDEX_FILE.read_bytes()
        except OSError:
            self._send_json(500, {"success": False, "error": "upload.html 缺失"})
            return
        self._send_bytes(200, "text/html; charset=utf-8", data)

    def _send_json(self, status: int, payload: dict) -> None:
        self._send_bytes(status, "application/json; charset=utf-8", _json_bytes(payload))

    def _send_bytes(self, status: int, content_type: str, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not API_KEY:
        logger.warning("未设置 ANYTHINGLLM_API_KEY：页面可打开，但接口会返回 503 提示。")
    logger.info("AnythingLLM 代理已启动：http://%s:%s -> %s", HOST, PORT, BASE_URL)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
