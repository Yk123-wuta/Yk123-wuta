# anythingllm-mcp

把本地或远程 **AnythingLLM** 实例封装为标准 **MCP Server**，让 AI Agent 可以通过 MCP 协议
管理工作区、上传并向量化文档、更新嵌入以及基于知识库回答问题。

- 传输方式：Streamable HTTP（默认 `http://127.0.0.1:8000/mcp`）
- 上游：AnythingLLM Developer API
- 鉴权：`ANYTHINGLLM_API_KEY` 只从环境变量读取，不写入源码
- 工具数量：**8 个**

## 目录结构

```text
AnythingLLM MCP/
├── README.md
├── .env.example
├── opencode.json
├── pyproject.toml
├── src/anythingllm_mcp/
│   ├── __init__.py
│   ├── __main__.py       python -m anythingllm_mcp 入口
│   ├── server.py         MCPServer 定义与 8 个 @mcp.tool
│   ├── client.py         AnythingLLM Developer API 客户端
│   ├── config.py         环境变量读取
│   └── models.py         工作区参数 payload 构造
└── tests/
    ├── test_client.py
    └── test_server.py
```

## 安装与启动

要求 Python 3.10+，AnythingLLM 已启动并能访问 Developer API。

```bash
cd "宠物医院LLM/AnythingLLM MCP"
pip install -e .
```

设置环境变量（PowerShell）：

```powershell
$env:ANYTHINGLLM_API_KEY = "你的 Developer API Key"
$env:ANYTHINGLLM_BASE_URL = "http://localhost:3001/api"   # 可选，默认值即此
```

启动：

```bash
anythingllm-mcp
# 等价入口：python -m anythingllm_mcp
# 也可以：python -m anythingllm_mcp.server
```

启动后 MCP 端点为 `http://127.0.0.1:8000/mcp`（可用 `MCP_HOST` / `MCP_PORT` 修改）。
`opencode.json` 中已给出对应的客户端配置示例：

```json
{
  "mcp": {
    "anythingllm": {
      "type": "remote",
      "url": "http://127.0.0.1:8000/mcp",
      "enabled": true
    }
  }
}
```

## 环境变量

| 变量 | 必需 | 默认值 | 说明 |
|---|---|---|---|
| `ANYTHINGLLM_API_KEY` | 是 | 无 | AnythingLLM Developer API Key；缺失时启动/首次调用会得到明确报错 |
| `ANYTHINGLLM_BASE_URL` | 否 | `http://localhost:3001/api` | AnythingLLM API 根地址（不要带结尾 `/`） |
| `ANYTHINGLLM_TIMEOUT` | 否 | `60` | 请求上游超时（秒） |
| `ANYTHINGLLM_WORKSPACE` | 否 | 空 | 默认工作区 slug；留空时自动使用实例中的第一个工作区 |
| `MCP_HOST` | 否 | `127.0.0.1` | MCP Streamable HTTP 监听地址 |
| `MCP_PORT` | 否 | `8000` | MCP Streamable HTTP 监听端口 |

模板见 [.env.example](.env.example)。

## 8 个工具

| # | 工具 | 对应 AnythingLLM API | 说明 |
|---|---|---|---|
| 1 | `list_workspaces` | `GET /v1/workspaces` | 列出所有工作区及其元数据 |
| 2 | `get_workspace` | `GET /v1/workspace/{slug}` | 按 slug 获取工作区详情；不存在时返回 `found=false` |
| 3 | `create_workspace` | `POST /v1/workspace/new` | 新建工作区，可设置相似度阈值、温度、历史轮数、系统提示、聊天模式、topN |
| 4 | `update_workspace` | `POST /v1/workspace/{slug}/update` | 更新工作区设置，只提交传入的字段 |
| 5 | `delete_workspace` | `DELETE /v1/workspace/{slug}` | 删除工作区 |
| 6 | `ask` | `POST /v1/workspace/{slug}/chat`（`mode="query"`） | 基于工作区知识库回答问题，返回答案与引用来源 |
| 7 | `upload_document` | `POST /v1/document/upload` + `POST /v1/workspace/{slug}/update-embeddings` | 上传 UTF-8 文本文档，加入工作区并触发向量化，返回文档 ID 与 `vectorized` 状态 |
| 8 | `update_workspace_embeddings` | `POST /v1/workspace/{slug}/update-embeddings` | 对已有文档位置执行 adds / deletes / reset，重新向量化或移除嵌入 |

### 工具参数

| 工具 | 参数 |
|---|---|
| `get_workspace` / `delete_workspace` | `slug`（必需） |
| `create_workspace` | `name`（必需）、`similarityThreshold`、`openAiTemp`、`openAiHistory`、`openAiPrompt`、`queryRefusalResponse`、`chatMode`（`automatic` / `query` / `chat`）、`topN` |
| `update_workspace` | `slug`（必需）+ 与 `create_workspace` 相同的可选配置字段、`name` |
| `ask` | `question`（必需） |
| `upload_document` | `filename`（必需，含扩展名）、`content`（必需，UTF-8 文本）、`add_to_workspace`（默认 `true`）、`workspace`（可选 slug） |
| `update_workspace_embeddings` | `workspace`（可选）、`adds`（文档 location 列表）、`deletes`、`reset`（bool）；三者至少提供一个 |

### 工作区解析顺序

需要工作区的工具按以下顺序选择 slug：

1. 工具参数 `workspace`
2. 环境变量 `ANYTHINGLLM_WORKSPACE`
3. 调用 `GET /v1/workspaces` 取第一个工作区；实例中没有工作区时返回 `MCPError(-32603)` 并提示先创建工作区

## 调用示例

MCP 客户端枚举工具后，按其 schema 传参即可。以下为各工具的典型参数：

```text
list_workspaces()

get_workspace(slug="my-workspace")

create_workspace(
    name="宠物医院知识库",
    similarityThreshold=0.25,
    openAiTemp=0.2,
    openAiHistory=20,
    chatMode="query",
    topN=4
)

update_workspace(slug="my-workspace", openAiTemp=0.1, topN=6)

delete_workspace(slug="my-workspace")

ask(question="宠物医院营业时间是什么？")

upload_document(
    filename="faq.md",
    content="# 常见问题\n\n营业时间为 9:00-21:00。",
    add_to_workspace=True,
    workspace="my-workspace"
)

update_workspace_embeddings(
    workspace="my-workspace",
    adds=["custom-documents/faq.md-abc.json"]
)
```

`upload_document` 的返回结构：

```json
{
  "workspace": "my-workspace",
  "uploaded_count": 1,
  "documents": [
    {
      "id": "faq.md-abc",
      "title": "faq.md",
      "location": "custom-documents/faq.md-abc.json",
      "vectorized": true
    }
  ],
  "embedding": "..."
}
```

## 错误与鉴权

- 缺少 `ANYTHINGLLM_API_KEY` 时，`load_config()` 直接抛出
  `RuntimeError: ANYTHINGLLM_API_KEY is not set...`，不会带着空密钥请求上游。
- 网络不可达或上游返回 4xx/5xx 时，客户端抛出 `AnythingLLMError`（`MCPError` 子类，
  JSON-RPC 错误码 `-32603`），消息中包含请求方法、路径、HTTP 状态码与上游响应摘要。
- `upload_document` 在上游返回 `success=false` 时抛出 `MCPError(-32603)` 并附带错误原因；
  `filename` 或 `content` 为空时抛出 `MCPError(-32602)`。
- `update_workspace_embeddings` 在未提供 `adds` / `deletes` / `reset=true` 时抛出 `MCPError(-32602)`。

## 测试

```bash
cd "宠物医院LLM/AnythingLLM MCP"
pip install -e ".[dev]"
pytest
```
