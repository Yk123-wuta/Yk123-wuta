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
import json
import logging
import math
import sys
from typing import Any

from mcp.server import MCPServer

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

logger = logging.getLogger(__name__)


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

    @mcp.tool()
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

    @mcp.tool()
    async def list_pets(
        q: str | None = None,
        name: str | None = None,
        ownerName: str | None = None,
        ownerPhone: str | None = None,
        species: str | None = None,
        breed: str | None = None,
        gender: str | None = None,
        ageMonths: int | None = None,
        ageMin: int | None = None,
        ageMax: int | None = None,
        doctor: str | None = None,
        disease: str | None = None,
        status: str | None = None,
        min: float | None = None,
        max: float | None = None,
        sortBy: str | None = None,
        order: str | None = None,
        page: int | None = None,
        pageSize: int | None = None,
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
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    mcp = build_server(base_url)
    await mcp.run_stdio_async()


async def _run_http(base_url: str, host: str, port: int, path: str) -> None:
    logging.basicConfig(level=logging.INFO)
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