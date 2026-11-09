"""MCPServer (protocol 2026-07-28) exposing the Pet Hospital REST API as MCP tools.

Current tools:
    * `list_pets` — list/filter pet records
    * `get_meta`  — enum dictionaries (species / gender / status / ...) to filter by

Transports: stdio (local agents) and Streamable HTTP (remote agents).

Usage:
    python -m hospital_mcp.server stdio
    python -m hospital_mcp.server http --port 8301 [--base-url http://127.0.0.1:8080]

The protocol revision 2026-07-28 is stateless: there is no initialize
handshake and no session. The official Python SDK (v2) handles the
per-request `_meta` metadata and `server/discover` automatically.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import json
import logging
import math
import sys
import time
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent
from pydantic import Field

from .config import config
from .upstream import HospitalClient, UpstreamError

SERVER_NAME = "pet-hospital-mcp"
SERVER_VERSION = "0.2.0"

# Filters the upstream applies server-side (passed straight through).
UPSTREAM_FILTER_KEYS = (
    "q",
    "name",
    "ownerName",
    "ownerPhone",
    "species",
    "doctor",
    "disease",
    "status",
    "min",
    "max",
)

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 500

# Enum values mirror `GET /api/v1/meta` (the authoritative source at runtime).
Species = Literal["犬", "猫", "兔", "鸟", "仓鼠", "爬宠", "其他"]
Gender = Literal["公", "母"]
Status = Literal["待就诊", "就诊中", "住院中", "已康复", "慢性病随访"]
SortOrder = Literal["asc", "desc"]

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

logger = logging.getLogger(__name__)


def setup_logging(log_file: str | None = None, level: str | None = None) -> None:
    """Configure file + stderr logging for the `hospital_mcp` logger.

    Safe to call repeatedly: handlers installed by a previous call are replaced.
    """
    base_logger = logging.getLogger("hospital_mcp")
    for handler in list(base_logger.handlers):
        if getattr(handler, "_hospital_mcp_handler", False):
            base_logger.removeHandler(handler)
            handler.close()

    log_path = Path(log_file or config.log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_level = getattr(
        logging, (level or config.log_level).upper(), logging.INFO
    )

    formatter = logging.Formatter(LOG_FORMAT)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler._hospital_mcp_handler = True  # type: ignore[attr-defined]

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    stream_handler._hospital_mcp_handler = True  # type: ignore[attr-defined]

    base_logger.setLevel(resolved_level)
    base_logger.addHandler(file_handler)
    base_logger.addHandler(stream_handler)
    base_logger.propagate = False
    logger.info(
        "logging configured file=%s level=%s",
        log_path,
        logging.getLevelName(resolved_level),
    )


def _summarize_args(args: dict[str, Any], limit: int = 240) -> str:
    """Return a compact, log-safe summary of the supplied tool arguments."""
    parts: list[str] = []
    for key, value in args.items():
        if value is None:
            continue
        text = str(value)
        if len(text) > 40:
            text = text[:37] + "..."
        parts.append(f"{key}={text}")
    summary = ", ".join(parts) or "(none)"
    return summary[:limit]


def _summarize_result(result: Any, limit: int = 120) -> str:
    """Return a compact summary of the tool payload for the call log."""
    if isinstance(result, str):
        text = result
    else:
        text = json.dumps(result, ensure_ascii=False, default=str)
    preview = text[:limit].replace("\n", " ")
    return f"chars={len(text)} preview={preview}"


def _upstream_error_result(exc: UpstreamError) -> CallToolResult:
    """Build an MCP error result carrying a stable error code and message."""
    error: dict[str, Any] = {
        "code": exc.code,
        "message": exc.message,
        "retryable": exc.retryable,
    }
    if exc.status_code is not None:
        error["status"] = exc.status_code
    if exc.detail:
        error["detail"] = exc.detail
    payload = json.dumps({"error": error}, ensure_ascii=False)
    return CallToolResult(
        content=[TextContent(type="text", text=payload)],
        isError=True,
    )


def _tool_call(tool_name: str):
    """Log every tool invocation and convert UpstreamError to a structured error.

    Records the tool name, argument summary, elapsed time and result summary;
    upstream failures become `CallToolResult(isError=True)` instead of leaking
    raw exceptions to the client.
    """

    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            logger.info(
                "tool=%s event=start args=%s",
                tool_name,
                _summarize_args(kwargs),
            )
            try:
                result = await fn(*args, **kwargs)
            except UpstreamError as exc:
                elapsed_ms = (time.perf_counter() - started) * 1000
                logger.error(
                    "tool=%s event=upstream_error code=%s status=%s elapsed_ms=%.1f message=%s",
                    tool_name,
                    exc.code,
                    exc.status_code,
                    elapsed_ms,
                    exc.message,
                )
                return _upstream_error_result(exc)
            except Exception:
                elapsed_ms = (time.perf_counter() - started) * 1000
                logger.exception(
                    "tool=%s event=crash elapsed_ms=%.1f",
                    tool_name,
                    elapsed_ms,
                )
                raise
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.info(
                "tool=%s event=ok elapsed_ms=%.1f result=%s",
                tool_name,
                elapsed_ms,
                _summarize_result(result),
            )
            return result

        return wrapper

    return decorator


def _matches_client_filters(
    item: dict[str, Any],
    breed: str | None,
    gender: str | None,
    age_months: int | None,
    age_min: int | None,
    age_max: int | None,
) -> bool:
    """Client-side predicates for dimensions the upstream does not filter."""
    if breed is not None and item.get("breed") != breed:
        return False
    if gender is not None and item.get("gender") != gender:
        return False
    age = item.get("ageMonths")
    if age_months is not None and age != age_months:
        return False
    if age_min is not None and (age is None or age < age_min):
        return False
    if age_max is not None and (age is None or age > age_max):
        return False
    return True


def _sort_key(item: dict[str, Any], field: str) -> tuple[int, float, str]:
    value = item.get(field)
    if isinstance(value, bool):
        return (0, float(value), "")
    if isinstance(value, (int, float)):
        return (0, float(value), "")
    if value is None:
        return (2, 0.0, "")
    return (1, 0.0, str(value))


def _sort_items(
    items: list[dict[str, Any]], sort_by: str, order: str
) -> list[dict[str, Any]]:
    reverse = order.lower() == "desc"
    return sorted(items, key=lambda it: _sort_key(it, sort_by), reverse=reverse)


def _paginate(
    items: list[dict[str, Any]], page: int, page_size: int
) -> tuple[list[dict[str, Any]], int]:
    total = len(items)
    total_pages = max(1, math.ceil(total / page_size)) if page_size else 1
    start = (page - 1) * page_size
    return items[start : start + page_size], total_pages


def build_server(base_url: str, client_factory=None) -> MCPServer:
    """Assemble the MCP server with its tool set.

    `client_factory` is an optional zero-argument callable returning a
    :class:`HospitalClient`; it exists to let tests inject a mock transport.
    """
    client = client_factory() if client_factory else HospitalClient(base_url)

    mcp = MCPServer(SERVER_NAME, version=SERVER_VERSION)

    # `structured_output=False`: tools return JSON text (or a structured error
    # result), so no output schema should be derived from the `-> str` hint.
    @mcp.tool(structured_output=False)
    @_tool_call("get_meta")
    async def get_meta() -> str:
        """获取筛选枚举字典 / Get enum dictionaries for filtering.

        返回系统中可用的动物种类、性别、就诊状态、可排序字段、收费分类，
        以及各字段的说明。**在调用 list_pets 前可先调用本工具**，
        以获取合法的筛选取值，避免传入无效值。

        Returns
        -------
        JSON，包含：
        - species: 动物种类列表，如 ["犬","猫","兔","鸟","仓鼠","爬宠","其他"]
        - gender: 性别列表，如 ["公","母"]
        - status: 就诊状态列表，如 ["待就诊","就诊中","住院中","已康复","慢性病随访"]
        - sortFields: list_pets 支持的排序字段
        - chargeCategories: 收费分类
        - fields: 档案字段说明
        """
        data = await client.get("/api/v1/meta")
        return json.dumps(data, ensure_ascii=False, default=str)

    @mcp.tool(structured_output=False)
    @_tool_call("list_pets")
    async def list_pets(
        q: str | None = None,
        name: str | None = None,
        ownerName: str | None = None,
        ownerPhone: str | None = None,
        species: Annotated[
            Species, Field(description="动物种类，枚举见 get_meta")
        ]
        | None = None,
        breed: str | None = None,
        gender: Annotated[Gender, Field(description="性别：公 / 母")]
        | None = None,
        ageMonths: Annotated[
            int, Field(ge=0, description="精确月龄（含），非负整数")
        ]
        | None = None,
        ageMin: Annotated[
            int, Field(ge=0, description="月龄下限（含），非负整数")
        ]
        | None = None,
        ageMax: Annotated[
            int, Field(ge=0, description="月龄上限（含），非负整数")
        ]
        | None = None,
        doctor: str | None = None,
        disease: str | None = None,
        status: Annotated[
            Status, Field(description="就诊状态，枚举见 get_meta")
        ]
        | None = None,
        min: Annotated[
            float, Field(ge=0, description="总花费下限（元，含），非负数")
        ]
        | None = None,
        max: Annotated[
            float, Field(ge=0, description="总花费上限（元，含），非负数")
        ]
        | None = None,
        sortBy: str | None = None,
        order: Annotated[
            SortOrder, Field(description="排序方向：asc 升序 / desc 降序")
        ]
        | None = None,
        page: Annotated[
            int, Field(ge=1, description="页码，从 1 开始")
        ]
        | None = None,
        pageSize: Annotated[
            int,
            Field(ge=1, le=MAX_PAGE_SIZE, description="每页条数，1-500"),
        ]
        | None = None,
    ) -> str:
        """列出/筛选宠物档案 / List and filter pet records.

        从宠物医院系统查询档案列表，可按种类、品种、性别、月龄、主人、
        医生、疾病、状态、花费区间等多项条件筛选，支持排序与分页。

        返回值形如：
        {"items":[...], "total":N, "page":1, "pageSize":20,
         "totalPages":M, "totalCost":X}

        Parameters
        ----------
        q : 全局关键字（匹配姓名/主人/疾病等多个字段）
        name : 按宠物姓名精确筛选
        ownerName : 按主人姓名筛选
        ownerPhone : 按主人电话筛选
        species : 按动物种类筛选，取值见 get_meta（如「犬」「猫」「兔」）
        breed : 按品种筛选，如「柯基」「金毛」「布偶」
        gender : 按性别筛选，「公」或「母」
        ageMonths : 按精确月龄筛选，如 36
        ageMin : 按月龄下限筛选（含），如 12
        ageMax : 按月龄上限筛选（含），如 60
        doctor : 按主治医生筛选，如「李医生」
        disease : 按疾病筛选，如「急性肠胃炎」
        status : 按就诊状态筛选，取值见 get_meta
        min : 按总花费下限筛选（数字，元）
        max : 按总花费上限筛选（数字，元）
        sortBy : 排序字段，取值见 get_meta 的 sortFields
        order : 排序方向，asc 升序 / desc 降序
        page : 页码（从 1 开始）
        pageSize : 每页条数（最大 500）

        Example
        -------
        list_pets(species="犬", breed="柯基", gender="公")
        list_pets(species="猫", ageMin=12, ageMax=60, sortBy="totalCost", order="desc")
        """
        upstream_params: dict[str, Any] = {}
        for key in UPSTREAM_FILTER_KEYS:
            value = locals()[key]
            if value is not None:
                upstream_params[key] = value

        client_filters = {
            "breed": breed,
            "gender": gender,
            "ageMonths": ageMonths,
            "ageMin": ageMin,
            "ageMax": ageMax,
        }
        has_client_filters = any(v is not None for v in client_filters.values())

        page_no = page if page and page > 0 else 1
        page_size = pageSize if pageSize and pageSize > 0 else DEFAULT_PAGE_SIZE
        if page_size > MAX_PAGE_SIZE:
            page_size = MAX_PAGE_SIZE

        # Fast path: everything the upstream can filter/sort/paginate itself.
        if not has_client_filters:
            upstream_params["page"] = page_no
            upstream_params["pageSize"] = page_size
            if sortBy is not None:
                upstream_params["sortBy"] = sortBy
            if order is not None:
                upstream_params["order"] = order
            data = await client.get("/api/v1/pets", params=upstream_params)
            return json.dumps(data, ensure_ascii=False, default=str)

        # Slow path: fetch all upstream matches, filter/sort/paginate locally.
        items = await client.fetch_all("/api/v1/pets", params=upstream_params)
        filtered = [
            it
            for it in items
            if _matches_client_filters(
                it, breed, gender, ageMonths, ageMin, ageMax
            )
        ]
        sort_field = sortBy or "id"
        sort_order = (order or "asc").lower()
        filtered = _sort_items(filtered, sort_field, sort_order)
        paged, total_pages = _paginate(filtered, page_no, page_size)

        result = {
            "items": paged,
            "total": len(filtered),
            "page": page_no,
            "pageSize": page_size,
            "totalPages": total_pages,
            "totalCost": sum(
                (it.get("totalCost") or 0) for it in filtered
            ),
        }
        return json.dumps(result, ensure_ascii=False, default=str)

    return mcp


async def _run_stdio(base_url: str) -> None:
    mcp = build_server(base_url)
    await mcp.run_stdio_async()


async def _run_http(base_url: str, host: str, port: int, path: str) -> None:
    mcp = build_server(base_url)
    await mcp.run_streamable_http_async(
        host=host,
        port=port,
        streamable_http_path=path,
        stateless_http=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pet-hospital-mcp",
        description=f"{SERVER_NAME} v{SERVER_VERSION} — MCP (2026-07-28) gateway for the Pet Hospital REST API",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help=f"宠物医院 REST API 地址（默认取环境变量 PET_HOSPITAL_URL，兜底 {config.pet_hospital_url}）",
    )
    sub = parser.add_subparsers(dest="transport", required=True, title="transport")

    stdio = sub.add_parser("stdio", help="通过标准输入输出与本地 Agent 通信")
    stdio.set_defaults(handler=_run_stdio)

    http = sub.add_parser("http", help="启动 Streamable HTTP 服务，供远程 Agent 访问")
    http.add_argument(
        "--host",
        default=config.http_host,
        help=f"监听地址（默认 {config.http_host}，可用环境变量 MCP_HTTP_HOST）",
    )
    http.add_argument(
        "--port",
        type=int,
        default=config.http_port,
        help=f"监听端口（默认 {config.http_port}，可用环境变量 MCP_HTTP_PORT）",
    )
    http.add_argument(
        "--path",
        default="/mcp",
        help="HTTP 挂载路径（默认 /mcp）",
    )
    http.set_defaults(handler=_run_http)
    return parser


def main(argv: list[str] | None = None) -> None:
    setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    base_url = (args.base_url or config.pet_hospital_url).rstrip("/")

    if args.handler is _run_stdio:
        asyncio.run(_run_stdio(base_url))
    else:
        asyncio.run(
            _run_http(base_url, host=args.host, port=args.port, path=args.path)
        )


if __name__ == "__main__":
    main()
