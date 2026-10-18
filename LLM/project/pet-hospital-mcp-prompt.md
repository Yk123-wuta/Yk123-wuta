# 任务：为「宠物医院」REST API 开发一个 MCP Server（协议版本 2026-07-28）· MVP

## 一、任务目标
把本地运行的「宠物医院 REST API」（Go 编写的单文件应用）桥接为一个 MCP Server，
让 AI Agent（MCP Client）能通过标准化的 MCP 工具消费宠物医院数据。

**本次为 MVP（最小可行版本）**：只实现一个核心读工具 `list_pets`
（列出宠物档案，支持按各种参数筛选）。**不要**一次性开发全部接口，
其余工具（`get_pet`、`get_stats`、`search_pets` 等）留到后续迭代。
但工程的目录结构、异步客户端封装、配置方式要**为后续快速新增工具预留好扩展点**。

开发语言：**Python**（3.10+）；传输方式：**同时支持 stdio 与 Streamable HTTP**。

## 二、上游服务背景（MVP 只需关注 + 预留扩展）
- 上游是已运行的宠物医院 HTTP 服务（可执行文件 `pethospital.exe`，默认 `http://127.0.0.1:8080`）。
- 统一响应信封：`{ "code": 200, "message": "ok", "data": {...}, "time": "..." }`。
- **MCP 工具返回给 Agent 的内容应解包为信封里的 `data`**（可直接消费的 JSON），
  出错时保留并返回上游 `code`/`message` 便于排查。
- 本次唯一要对接的接口：**`GET /api/v1/pets`**（档案列表），支持通用查询参数：
  `q name ownerName ownerPhone species doctor disease status min max sortBy order page pageSize`，
  全部为可选；响应 `data` 的字段结构（如 items/total/page 等）代码里不要硬编码假设，
  以运行期实际返回为准，但要保证完整透传给 Agent。
- 合法枚举值（种类/疾病/医生/就诊状态）**不要硬编码**，可在工具描述里提示 Agent
  调用后续迭代将要提供的元数据能力，或用环境允许的方式从 `GET /api/v1/meta` 自查。
  MVP 阶段**不需要**新增其他工具。
- 完整接口清单可从 `GET /api/v1/endpoints` 获取（JSON），供了解全貌、规划后续迭代用。

### MVP 工具清单（只有一个）
| MCP 工具名 | 上游接口 | 参数 |
| --- | --- | --- |
| `list_pets` | GET /api/v1/pets | 全部可选：q,name,ownerName,ownerPhone,species,doctor,disease,status,min,max,sortBy,order,page,pageSize |

工具参数命名为上游同名字段（蛇形命名）；输入 Schema 用 **JSON Schema 2020-12**。
`list_pets` 必须有**清晰的中文+英文双语文档字符串**：写明用途、
每个参数含义与取值示例（如 species=犬、status=待就诊、sortBy=totalCost 等），
便于 Agent 正确筛选。

## 三、【最高优先级】必须严格遵守 MCP 2026-07-28 协议（断代式改动，禁止再用旧写法）

这是自 2024-11-05 以来最大的协议修订，2026-07-28 已把协议核心改为**无状态（stateless）**。
编写时必须遵守，**不得**套用 2025 年之前的教程/旧 SDK 示例：

1. **禁止** `initialize` / `notifications/initialized` 握手；**禁止** `Mcp-Session-Id` 会话机制；
   **禁止**任何 session 状态管理代码。每个请求都是自包含的独立请求。
2. 协议版本、客户端能力改为**每个请求在 `_meta` 里自带**：
   `_meta.io.modelcontextprotocol/protocolVersion`、`io.modelcontextprotocol/clientInfo`、`io.modelcontextprotocol/clientCapabilities`。
3. **必须实现 `server/discover`**：服务器向客户端宣告支持的协议版本、能力（capabilities）与身份。
   这是新协议唯一新增的必选 RPC（官方 SDK 通常已内建，不要手动重复实现）。
4. **已移除** `ping`、`logging/setLevel`、`notifications/roots/list_changed`；不要把这三个方法写进服务。
5. **roots、sampling(createMessage)、logging 已被官方弃用（SEP-2577）**，新开发一律不得使用。
6. 工具入参 Schema 采用 **JSON Schema 2020-12** 规范（不是旧 draft-04/07）。
7. 所有 RPC 响应都必须带 `resultType` 字段（`"complete"` / `"input_required"`）——用官方 SDK 自动生成即可，不要手写。
8. Streamable HTTP 传输的强制变化（SEP-2243）：
   - POST 请求必须带头：`MCP-Protocol-Version: 2026-07-28`、`Mcp-Method: <method>`、`Mcp-Name: <任意调用名>`。
   - 响应也要回带 `MCP-Protocol-Version` 头。
   - 不支持协议版本时返回 `UnsupportedProtocolVersionError`（含服务器支持的版本列表）。
   - **禁止**产生任何 `Mcp-Session-Id` 请求/响应头。
9. 变更通知从旧 GET 端点 + `resources/subscribe` 改为 `subscriptions/listen` 单一长连接流——
   本项目为只读桥接，声明不支持订阅即可。
10. `tools/list`/`prompts/list`/`resources/list` 等列表结果的缓存语义由 `ttlMs`/`cacheScope` 表达（库自动处理）。

## 四、技术选型与工程约束
- 使用官方 **Python MCP SDK v2**（PyPI 包名 `mcp`，版本 **>=2.0**）。注意 v2 是重写版：
  - 高层的 `FastMCP` 已改名为 **`MCPServer`**，导入路径为 `from mcp.server import MCPServer`（v1 的
    `from mcp.server.fastmcp import FastMCP` 已不存在，不要用）。
  - v2 自带对 2026-07-28 与旧版客户端的双时代兼容，无需自己写握手逻辑。
  - 先读官方文档 https://py.sdk.modelcontextprotocol.io/whats-new 与 Get started，再写代码。
- HTTP 客户端用 **httpx**（异步）；Streamable HTTP 服务用 **Starlette + uvicorn**（mcp 包已依赖 starlette）。
- 上游调用全部走**异步 HTTP**，超时设 10s；上游不可达时报清晰错误（如「无法连接宠物医院 http://127.0.0.1:8080，请先启动 pethospital.exe」）。
- 配置（均可被环境变量覆盖）：`PET_HOSPITAL_URL`（默认 `http://127.0.0.1:8080`）、`MCP_HTTP_HOST`（默认 `127.0.0.1`）、`MCP_HTTP_PORT`（默认 `8301`）。
- MCP 服务器身份：name=`pet-hospital-mcp`，version=`0.1.0`（MVP）。
- 工程为独立项目，目录骨架（异步客户端与工具注册要便于后续加工具）：
  ```text
  pet-hospital-mcp/
  ├── pyproject.toml          # 依赖、entrypoint、scripts 配置
  ├── README.md               # 启动方法（两种传输）、当前工具清单、与 pethospital.exe 的搭配说明
  ├── src/hospital_mcp/
  │   ├── __init__.py
  │   ├── config.py           # 环境变量/CLI 配置
  │   ├── upstream.py         # 异步 REST 客户端：统一请求/解包 data/超时/错误归一化（后续工具复用）
  │   └── server.py           # MCPServer 定义 + @mcp.tool()（本次仅 list_pets）+ 入口
  └── tests/test_tools.py     # 覆盖 list_pets 的成功路径、筛选参数、上游不可达错误路径
  ```
- 入口 `server.py` 支持子命令/参数：`python -m hospital_mcp.server stdio` 与
  `... http`（起 uvicorn，挂载路径 `/mcp`），并可 `... http --port 8301`、`--base-url ...`。

## 五、验证标准（必须全部通过才算完成）
1. 启动上游：`pethospital.exe`，确认 http://127.0.0.1:8080/health 可用。
2. HTTP 传输验证（演示 2026-07-28 无状态调用，curl 或 httpx）：
   ```bash
   curl -s -X POST http://127.0.0.1:8301/mcp \
     -H "Content-Type: application/json" \
     -H "MCP-Protocol-Version: 2026-07-28" \
     -H "Mcp-Method: tools/list" \
     -H "Mcp-Name: discover" \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientInfo":{"name":"curl","version":"1.0"}}}}'
   ```
   `tools/list` 应只返回 `list_pets` 一个工具（含完整参数 Schema）。
   再用 `tools/call` 调 `list_pets`：不带参数 / 带筛选参数（如 species=犬、status=待就诊、pageSize=5）
   各演示一次，确认参数能正确透传给上游并返回完整 `data`。
   用 `server/discover` 验证 capability 宣告正常。
3. stdio 传输验证：用 SDK Client 以 `mode="2026-07-28"`（或 auto）启动子进程连接本服务，
   断言 `client.protocol_version == "2026-07-28"`，成功调用 `list_pets` 且筛选生效。
4. 响应里**绝对不允许**出现 `Mcp-Session-Id` 头；服务端不得实现/响应旧握手流程。
5. 跑通 `pip install -e .` + `pytest`（覆盖新协议连接与 list_pets 调用）。

## 六、参考（按需查阅，但以三/四为准）
- 规范（最新 2026-07-28）：https://modelcontextprotocol.io/specification/2026-07-28
- 变更清单：https://modelcontextprotocol.io/specification/2026-07-28/changelog
- 官方 Python SDK v2 文档：https://py.sdk.modelcontextprotocol.io/ （重点：What's new in v2、Get started、Migration guide）

## 七、交付清单（MVP 范围，勿超范围实现）
代码 + README + pyproject.toml + 测试；并给出两条能直接运行的启动命令
（stdio 供 Claude Desktop/opencode 等本机 Agent 用，http 供远程 Agent 用）。
完成后按「五、验证标准」逐条演示结果。
其余接口（get_pet/get_stats/search_pets/top_spenders 等）**不在本次范围**，仅需在代码注释或 README
中说明后续迭代计划即可，不要实现。