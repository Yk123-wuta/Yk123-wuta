"""Tests for the MCP server (protocol 2026-07-28): list_pets + get_meta."""

import json
import math

import httpx
import pytest

from hospital_mcp.server import build_server
from hospital_mcp.upstream import HospitalClient, UpstreamError

BASE_URL = "http://127.0.0.1:8080"

PETS = [
    {"id": "PET-000001", "name": "旺财", "species": "犬", "breed": "柯基",
     "gender": "公", "ageMonths": 36, "doctor": "李医生", "disease": "急性肠胃炎",
     "status": "待就诊", "totalCost": 1000.0, "visitCount": 2},
    {"id": "PET-000002", "name": "咪咪", "species": "猫", "breed": "布偶",
     "gender": "母", "ageMonths": 12, "doctor": "王医生", "disease": "猫瘟",
     "status": "住院中", "totalCost": 5000.0, "visitCount": 3},
    {"id": "PET-000003", "name": "豆豆", "species": "犬", "breed": "金毛",
     "gender": "公", "ageMonths": 60, "doctor": "李医生", "disease": "骨折",
     "status": "已康复", "totalCost": 3000.0, "visitCount": 1},
    {"id": "PET-000004", "name": "球球", "species": "兔", "breed": "垂耳兔",
     "gender": "母", "ageMonths": 6, "doctor": "赵医生", "disease": "球虫",
     "status": "待就诊", "totalCost": 200.0, "visitCount": 1},
]

META = {
    "species": ["犬", "猫", "兔"],
    "gender": ["公", "母"],
    "status": ["待就诊", "住院中", "已康复"],
    "sortFields": ["id", "name", "species", "totalCost"],
    "chargeCategories": ["检查", "药品"],
    "fields": [{"name": "breed", "desc": "品种"}],
}


def envelope(data, code=200, message="ok"):
    return {"code": code, "message": message, "data": data, "time": "2026-01-01T00:00:00+08:00"}


def dataset_handler(pets=None):
    pets = pets if pets is not None else PETS

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/meta":
            return httpx.Response(200, json=envelope(META))
        if request.url.path != "/api/v1/pets":
            return httpx.Response(404, json={"code": 404, "message": "not found"})

        params = request.url.params
        items = list(pets)
        if params.get("species"):
            items = [p for p in items if p["species"] == params["species"]]
        sort_by = params.get("sortBy")
        if sort_by:
            reverse = params.get("order", "asc").lower() == "desc"
            items = sorted(items, key=lambda p: p.get(sort_by), reverse=reverse)
        page = int(params.get("page", 1))
        page_size = int(params.get("pageSize", 20))
        total = len(items)
        total_pages = max(1, math.ceil(total / page_size))
        start = (page - 1) * page_size
        return httpx.Response(200, json=envelope({
            "items": items[start:start + page_size],
            "total": total,
            "page": page,
            "pageSize": page_size,
            "totalPages": total_pages,
            "totalCost": sum(p["totalCost"] for p in items),
        }))

    return handler


def server(handler=None):
    h = handler or dataset_handler()
    return build_server(
        BASE_URL,
        client_factory=lambda: HospitalClient(
            BASE_URL, transport=httpx.MockTransport(h)
        ),
    )


async def call(mcp, name, args=None):
    res = await mcp.call_tool(name, args or {})
    return json.loads(res.content[0].text)


# --- tool surface ---------------------------------------------------------

async def test_list_tools_exposes_both_tools():
    mcp = server()
    tools = await mcp.list_tools()
    assert {t.name for t in tools} == {"list_pets", "get_meta"}


async def test_tool_result_has_complete_result_type():
    mcp = server()
    res = await mcp.call_tool("get_meta", {})
    assert res.result_type == "complete"


# --- get_meta -------------------------------------------------------------

async def test_get_meta_returns_enum_dictionaries():
    mcp = server()
    data = await call(mcp, "get_meta")
    assert data["species"] == ["犬", "猫", "兔"]
    assert data["gender"] == ["公", "母"]
    assert data["status"] == ["待就诊", "住院中", "已康复"]


# --- list_pets fast path (upstream-supported filters) ---------------------

async def test_list_pets_fast_path_species():
    mcp = server()
    data = await call(mcp, "list_pets", {"species": "犬", "pageSize": 1})
    assert data["total"] == 2
    assert data["totalCost"] == 4000.0
    assert len(data["items"]) == 1


async def test_list_pets_fast_path_omits_unset_params():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(200, json=envelope({
            "items": [], "total": 0, "page": 1, "pageSize": 20,
            "totalPages": 1, "totalCost": 0,
        }))

    mcp = server(handler)
    await call(mcp, "list_pets", {"page": 1})
    assert captured == {"page": "1", "pageSize": "20"}


# --- list_pets slow path (client-side filters) ----------------------------

async def test_filter_by_breed():
    mcp = server()
    data = await call(mcp, "list_pets", {"breed": "柯基"})
    assert data["total"] == 1
    assert data["items"][0]["id"] == "PET-000001"


async def test_filter_by_gender():
    mcp = server()
    data = await call(mcp, "list_pets", {"gender": "母"})
    assert data["total"] == 2
    assert {i["id"] for i in data["items"]} == {"PET-000002", "PET-000004"}


async def test_filter_by_age_range():
    mcp = server()
    data = await call(mcp, "list_pets", {"ageMin": 12, "ageMax": 60})
    assert data["total"] == 3
    assert {i["ageMonths"] for i in data["items"]} == {12, 36, 60}


async def test_filter_by_exact_age():
    mcp = server()
    data = await call(mcp, "list_pets", {"ageMonths": 36})
    assert data["total"] == 1
    assert data["items"][0]["name"] == "旺财"


async def test_client_filter_combined_with_upstream_species():
    mcp = server()
    data = await call(mcp, "list_pets", {"species": "犬", "breed": "柯基"})
    assert data["total"] == 1
    assert data["items"][0]["id"] == "PET-000001"


async def test_client_filter_sort_and_paginate():
    mcp = server()
    data = await call(
        mcp,
        "list_pets",
        {"gender": "公", "sortBy": "totalCost", "order": "desc",
         "page": 1, "pageSize": 1},
    )
    assert data["total"] == 2
    assert data["totalPages"] == 2
    assert data["items"][0]["totalCost"] == 3000.0


async def test_client_filter_no_match_returns_empty():
    mcp = server()
    data = await call(mcp, "list_pets", {"breed": "不存在"})
    assert data["total"] == 0
    assert data["items"] == []


# --- upstream client error handling ---------------------------------------

async def test_upstream_unwraps_data():
    client = HospitalClient(
        BASE_URL, transport=httpx.MockTransport(dataset_handler())
    )
    data = await client.get("/api/v1/meta")
    assert data["species"] == ["犬", "猫", "兔"]


async def test_upstream_business_error():
    client = HospitalClient(
        BASE_URL,
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json=envelope({}, code=500, message="boom"))
        ),
    )
    with pytest.raises(UpstreamError) as exc:
        await client.get("/api/v1/pets")
    assert "boom" in str(exc.value)


async def test_upstream_connection_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = HospitalClient(BASE_URL, transport=httpx.MockTransport(handler))
    with pytest.raises(UpstreamError):
        await client.get("/api/v1/pets")


async def test_upstream_http_error():
    client = HospitalClient(
        BASE_URL,
        transport=httpx.MockTransport(lambda r: httpx.Response(503, text="unavailable")),
    )
    with pytest.raises(UpstreamError) as exc:
        await client.get("/api/v1/pets")
    assert exc.value.status_code == 503