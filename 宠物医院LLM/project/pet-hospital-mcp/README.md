# 🐾 pet-hospital-mcp

MCP（Model Context Protocol，**2026-07-28 版本**）服务器，把本地运行中的
**宠物医院 REST API**（`pethospital.exe`）桥接为标准化 MCP 工具，让 AI Agent
（MCP Client）能直接查询宠物档案数据。

> 当前进度：已实现 `list_pets`（列出宠物档案 + 多维筛选）与 `get_meta`（筛选枚举字典）。
> 其余接口（get_pet / get_stats / search_pets / top_spenders 等）将在后续迭代加入。

## 协议版本说明（重要）

协议核心采用 **2026-07-28 无状态版本**：

- **无** `initialize` / `initialized` 握手、**无** `Mcp-Session-Id` 会话机制
- 协议版本与客户端能力由每个请求的 `_meta` 携带，服务器实现 `server/discover`
- 依赖官方 **Python MCP SDK v2**（`mcp>=2.0`），会自动处理上述新协议语义，
  并兼容 2025 及更早版本的客户端

## 快速开始

### 0. 启动上游服务

先运行宠物医院服务（默认监听 `http://127.0.0.1:8080`）：

```bash
pethospital.exe
# 或使用编译版对应平台的可执行文件；确认 http://127.0.0.1:8080/health 可用
```

### 1. 安装

需要 Python 3.10+。

```bash
cd pet-hospital-mcp
pip install -e .
```

### 2. 启动（两种传输方式，任选其一）

**stdio（本机 Agent，如 Claude Desktop / opencode）**：

```bash
pet-hospital-mcp stdio
```

**Streamable HTTP（远程 Agent）**：

```bash
pet-hospital-mcp http --port 8301 --base-url http://127.0.0.1:8080
# 挂载点： http://127.0.0.1:8301/mcp
```

也可用模块方式运行：

```bash
python -m hospital_mcp.server stdio
python -m hospital_mcp.server http --port 8301
```

### 3. 配置（环境变量，均可选）

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PET_HOSPITAL_URL` | `http://127.0.0.1:8080` | 宠物医院 REST API 地址 |
| `MCP_HTTP_HOST` | `127.0.0.1` | HTTP 监听地址 |
| `MCP_HTTP_PORT` | `8301` | HTTP 监听端口 |

## MCP 工具清单

| 工具 | 上游接口 | 说明 |
| --- | --- | --- |
| `get_meta` | `GET /api/v1/meta` | 获取可筛选枚举：`species`、`gender`、`status`、`sortFields`、`chargeCategories`、字段说明。建议先调用它拿到合法取值 |
| `list_pets` | `GET /api/v1/pets` | 列出/筛选宠物档案（参数全部可选） |

`list_pets` 参数：

- 上游支持（服务端筛选）：`q`, `name`, `ownerName`, `ownerPhone`, `species`,
  `doctor`, `disease`, `status`, `min`, `max`, `sortBy`, `order`, `page`, `pageSize`
- 由 MCP 层客户端筛选：`breed`（品种）、`gender`（性别）、`ageMonths`（精确月龄）、
  `ageMin` / `ageMax`（月龄区间）

> 说明：上游 `/api/v1/pets` 不支持 `breed`/`gender`/`ageMonths` 筛选。
> 当传入这些参数时，MCP 服务器会拉取全部匹配记录后在本层完成筛选、排序与分页，
> 返回结构与其他情况一致（`items` / `total` / `page` / `pageSize` / `totalPages` / `totalCost`）。
> 数据量较大时会多几次上游请求。

示例：

- `get_meta()`：先了解有哪些种类/性别/状态可筛选
- `list_pets(species="犬", status="待就诊", pageSize=10)`：按种类与状态筛选
- `list_pets(breed="柯基", gender="公")`：按品种与性别筛选
- `list_pets(species="猫", ageMin=12, ageMax=60, sortBy="totalCost", order="desc")`：按月龄区间 + 排序
- `list_pets(ownerPhone="13800001111")`：按主人电话查询
- `list_pets(min=1000, max=5000, sortBy="totalCost", order="desc")`：按总花费区间 + 排序

## 手动验证（Streamable HTTP，2026-07-28 无状态调用）

> 注意：2026-07-28 要求每个请求的 `_meta` 必须带
> `io.modelcontextprotocol/protocolVersion`、`/clientInfo`、`/clientCapabilities` 三个键；
> `tools/call` 的 `Mcp-Name` 头必须与请求体里的 `name` 参数一致。

```bash
curl -s -X POST http://127.0.0.1:8301/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "MCP-Protocol-Version: 2026-07-28" \
  -H "Mcp-Method: tools/list" \
  -H "Mcp-Name: discover" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientInfo":{"name":"curl","version":"1.0"},"io.modelcontextprotocol/clientCapabilities":{}}}}'
```

```bash
curl -s -X POST http://127.0.0.1:8301/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "MCP-Protocol-Version: 2026-07-28" \
  -H "Mcp-Method: tools/call" \
  -H "Mcp-Name: list_pets" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"list_pets","arguments":{"species":"犬","pageSize":5},"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientInfo":{"name":"curl","version":"1.0"},"io.modelcontextprotocol/clientCapabilities":{}}}}'
```

## 测试

```bash
pip install -e ".[dev]"
pytest
```

## 后续迭代计划

预计依次加入：`get_pet`、`get_stats`、`search_pets`、`pets_by_owner`、
`pets_by_doctor`、`top_spenders`、`get_pet_records`、`get_pet_charges`、
`get_pet_summary`、`export_data`，以及可选的写工具（`create_pet` 等）。