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
| `PET_HOSPITAL_TIMEOUT` | `10` | 单次上游请求超时（秒） |
| `PET_HOSPITAL_MAX_RETRIES` | `2` | 超时 / HTTP 5xx 的最大重试次数（总尝试 = 重试 + 1） |
| `PET_HOSPITAL_RETRY_BACKOFF` | `0.15` | 重试指数退避基数（秒），第 n 次重试等待 `base * 2^n` |
| `MCP_LOG_FILE` | `logs/pet-hospital-mcp.log` | 日志文件路径（相对当前工作目录） |
| `MCP_LOG_LEVEL` | `INFO` | 日志级别（`DEBUG` / `INFO` / `WARNING` / `ERROR`） |

也可用命令行参数覆盖地址与端口：

```bash
pet-hospital-mcp http --base-url http://127.0.0.1:8080 --host 127.0.0.1 --port 8301 --path /mcp
```

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

## 参数约束（工具 schema）

工具 schema 直接由类型注解 + `pydantic.Field` 生成，客户端 `tools/list` 能看到以下约束，
不需要先读业务代码：

| 参数 | 约束 |
| --- | --- |
| `species` | 枚举：`犬`、`猫`、`兔`、`鸟`、`仓鼠`、`爬宠`、`其他` |
| `gender` | 枚举：`公`、`母` |
| `status` | 枚举：`待就诊`、`就诊中`、`住院中`、`已康复`、`慢性病随访` |
| `order` | 枚举：`asc`、`desc` |
| `page` | 整数，最小 `1` |
| `pageSize` | 整数，`1-500`（服务端另有默认值 20） |
| `ageMonths` / `ageMin` / `ageMax` | 整数，最小 `0` |
| `min` / `max` | 数值，最小 `0` |
| `breed` / `sortBy` | 自由文本（`sortBy` 的合法取值来自 `get_meta` 的 `sortFields`） |

枚举取值以运行时 `GET /api/v1/meta` 为准；`get_meta` 工具返回同一份枚举字典。

## 结构化错误

工具内部会捕获 `UpstreamError`，返回 `CallToolResult(isError=True)`，文本内容为固定 JSON 结构：

```json
{
  "error": {
    "code": "UPSTREAM_UNAVAILABLE",
    "message": "无法连接宠物医院 http://127.0.0.1:8080，请先启动 pethospital.exe",
    "retryable": false,
    "status": 503,
    "detail": "上游错误摘要（已去除 HTML 标签与多余空白）"
  }
}
```

错误码：

| `code` | 触发场景 |
| --- | --- |
| `UPSTREAM_UNAVAILABLE` | 无法建立连接，或上游未启动 |
| `UPSTREAM_TIMEOUT` | 重试后仍请求超时 |
| `UPSTREAM_HTTP_ERROR` | 上游返回 4xx / 5xx（5xx 会先按配置重试） |
| `UPSTREAM_BUSINESS_ERROR` | 上游 HTTP 200 但业务 `code != 200` |
| `UPSTREAM_INVALID_RESPONSE` | 上游响应不是合法 JSON |

`retryable` 表示该错误是否由可重试场景产生，`status` 为上游 HTTP 状态码或业务码（若存在），
`detail` 只保留清洗后的短摘要，不会把上游 HTML 错误页或 traceback 原样透出。

## 重试、超时与日志

- 超时：`PET_HOSPITAL_TIMEOUT`，默认 10 秒。
- 重试：仅对**连接超时**与 **HTTP 5xx** 生效，最多 `PET_HOSPITAL_MAX_RETRIES` 次（默认 2），
  退避为 `PET_HOSPITAL_RETRY_BACKOFF * 2^n`（默认基数 0.15 秒）。
- 日志：`setup_logging` 在启动时同时挂载文件与 stderr 两个 handler，
  文件默认 `logs/pet-hospital-mcp.log`，级别取 `MCP_LOG_LEVEL`。
  每次工具调用记录 `event=start`（工具名 + 入参摘要）、`event=ok`（耗时 + 结果摘要）或
  `event=upstream_error`（错误码 / 状态 / 耗时 / 原因）；上游重试记录 `upstream retry ...`。
- 日志使用标准库 `logging`，不使用 `print`。

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