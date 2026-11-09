# Yk123-wuta

以 AI Agent、MCP（Model Context Protocol）和本地知识库/业务数据接入为主题的学习与实践仓库。

本仓库包含四个可直接运行的模块：

| 模块 | 目录 | 定位 |
|---|---|---|
| 定制 Agent（xAgent） | `开发agent/Agent/xAgent/` | 分层架构的文件操作 Agent：18 个本地工具、技能加载、MCP 客户端、AGENTS.md 注入、会话与上下文压缩、Textual TUI |
| 宠物医院 MCP | `宠物医院LLM/project/pet-hospital-mcp/` | 把宠物医院 REST API 封装为 MCP Server（2 个业务工具），支持 stdio 与 Streamable HTTP |
| AnythingLLM MCP | `宠物医院LLM/AnythingLLM MCP/` | 把 AnythingLLM 的工作区、文档上传/向量化、知识库问答封装为 MCP Server（8 个工具） |
| AnythingLLM Webpage | `宠物医院LLM/AnythingLLM Sever/` | 单页上传工具 + 本地代理，浏览器不接触 API Key |

此外还有一个入门聊天脚本 `Agent/Practice01/`，演示如何调用 OpenAI 兼容接口。

---

## 项目结构

```text
Yk123-wuta/
├── Agent/
│   └── Practice01/                      入门聊天脚本（OpenAI 兼容接口）
│       ├── chat.py
│       └── README.md
├── 宠物医院LLM/
│   ├── README.md                        模块总览
│   ├── AnythingLLM MCP/                 MCP Server（8 个工具）
│   │   ├── README.md
│   │   ├── .env.example
│   │   ├── opencode.json
│   │   ├── pyproject.toml
│   │   ├── src/anythingllm_mcp/
│   │   └── tests/
│   ├── AnythingLLM Sever/               网页上传页面 + 本地代理
│   │   ├── README.md
│   │   ├── serve.py
│   │   └── upload.html
│   └── project/
│       ├── opencode.json
│       ├── pet-hospital-mcp-prompt.md
│       ├── 宠物清单摘要.txt
│       ├── pet-hospital-mcp/            MCP Server（2 个工具）
│       │   ├── README.md
│       │   ├── pyproject.toml
│       │   ├── src/hospital_mcp/
│       │   └── tests/
│       └── pet-hospital-windows-amd64/
│           └── windows/                 上游预编译程序与数据库（非自研代码）
│               ├── pethospital.exe
│               ├── data/pet.db
│               ├── LICENSE
│               ├── README.md
│               └── README-Windows.md
├── 开发agent/
│   └── Agent/
│       ├── Practice02/                  文件操作 Agent 原型与回归测试
│       └── xAgent/                      分层文件操作 Agent（core + tui）
│           ├── README.md
│           ├── requirements.txt
│           ├── main.py
│           ├── core/
│           ├── tui/
│           └── tests/
└── README.md
```

> `pethospital.exe`（约 6.5MB）与 `data/pet.db`（约 2.4MB）是课程/上游提供的**预编译可执行程序与数据库文件**，随上游 LICENSE 与 README 一同分发，**不是本仓库自行开发的代码**。仓库保留它们仅用于本地联调，请勿将其视为自研成果。

> 仓库另有一个历史归档目录 `新建文件夹/`，其中只保留了 `Agent/Practice02/chat-stream-tool-fileops.py` 的一份副本。
> 经 MD5 校验，它与 `开发agent/Agent/Practice02/chat-stream-tool-fileops.py` 完全一致
> （`EF541263140ABA6C7ECA5D22ABA943CC`）。是否移除该副本涉及 Git 提交，需由维护者确认后另行处理。


---

## 模块关系

```text
xAgent（MCP Client）
   ├─ stdio / Streamable HTTP ──> 宠物医院 MCP ──HTTP──> pethospital.exe (127.0.0.1:8080)
   └─ Streamable HTTP ──────────> AnythingLLM MCP ──HTTP──> AnythingLLM (localhost:3001/api)

AnythingLLM Webpage（upload.html）
   └─ 同源请求 ──> 本地代理 serve.py ──Bearer Key──> AnythingLLM Developer API
```

- xAgent 通过工作目录下的 `.mcp.json` 接入任一 MCP Server；连接失败会降级并在 TUI 中显示状态。
- 两个 MCP Server 均可独立启动，也可同时挂到同一个客户端。
- Webpage 模块不经过 MCP，直接由本地代理访问 AnythingLLM API。

---

## 环境依赖

- Python 3.10+
- 宠物医院 MCP：`mcp>=2.0`、`httpx>=0.27`、`pydantic>=2.0`、`uvicorn>=0.30`
- AnythingLLM MCP：`mcp>=2.2.0`、`httpx2>=2.0.0`
- xAgent：`textual>=8.0`、`trafilatura>=2.0`、`mcp>=2.0`（`core` 文件工具本身只用标准库）
- AnythingLLM Sever：仅用 Python 标准库
- 上游服务：本地运行的宠物医院 `pethospital.exe`（默认 `http://127.0.0.1:8080`）与 AnythingLLM 实例（Developer API 默认 `http://localhost:3001/api`）

---

## 配置与密钥

- 所有真实密钥都通过**环境变量或本地配置文件**注入，不写入源码、不提交到仓库。
- AnythingLLM MCP 提供了配置模板 [.env.example](宠物医院LLM/AnythingLLM%20MCP/.env.example)。
- AnythingLLM Webpage 的 Key 只存在于 `serve.py` 进程的环境变量中，前端页面不保存、不硬编码。
- xAgent 首次运行会在工作目录生成 `config.ini`（含 Mock 配置），请在本地填写后再启动，并确保该文件不被提交。
- 若你曾在历史提交中暴露过真实 API Key，删除代码并不等于密钥失效，请到服务控制台吊销并重新生成。

---

## 分步启动

### 1. 入门聊天脚本（Agent/Practice01）

```bash
cd Agent/Practice01
pip install openai
# 在 chat.py 同目录创建 config.ini（该文件被忽略，不入库）：
#   [default]
#   base_url=https://api.deepseek.com
#   model=deepseek-chat
#   api_key=sk-xxx
python chat.py
```

### 2. 宠物医院 MCP

前置：先启动宠物医院上游服务，确认 `http://127.0.0.1:8080/health` 可用。

```bash
cd "宠物医院LLM/project/pet-hospital-mcp"
pip install -e .
pet-hospital-mcp http --port 8301          # Streamable HTTP: http://127.0.0.1:8301/mcp
# 或：pet-hospital-mcp stdio               # 供本地 Agent 以子进程方式接入
```

上游地址可用 `PET_HOSPITAL_URL` 覆盖；完整环境变量、错误格式与参数约束见
[pet-hospital-mcp/README.md](宠物医院LLM/project/pet-hospital-mcp/README.md)。

### 3. AnythingLLM MCP

前置：AnythingLLM 已启动，并在 Settings → Tools 中生成 Developer API Key。

```bash
cd "宠物医院LLM/AnythingLLM MCP"
pip install -e .
# PowerShell 示例：
$env:ANYTHINGLLM_API_KEY = "你的 Developer API Key"
$env:ANYTHINGLLM_BASE_URL = "http://localhost:3001/api"   # 可选
anythingllm-mcp                            # 默认 Streamable HTTP: http://127.0.0.1:8000/mcp
# 等价入口：python -m anythingllm_mcp
```

8 个工具、每个工具的入参和调用示例见
[AnythingLLM MCP/README.md](宠物医院LLM/AnythingLLM%20MCP/README.md)。

### 4. AnythingLLM 网页上传（AnythingLLM Sever）

```bash
cd "宠物医院LLM/AnythingLLM Sever"
# PowerShell 示例：
$env:ANYTHINGLLM_API_KEY = "你的 Developer API Key"
$env:ANYTHINGLLM_BASE_URL = "http://localhost:3001/api"   # 可选
python serve.py
# 浏览器打开 http://127.0.0.1:3002/
```

页面会从代理拉取工作区列表、上传文件并触发生成向量；所有动态内容使用 `textContent` 写入。
详见 [AnythingLLM Sever/README.md](宠物医院LLM/AnythingLLM%20Sever/README.md)。

### 5. xAgent

```bash
cd 开发agent/Agent
pip install -r xAgent/requirements.txt
python xAgent/main.py        # 启动 Textual TUI
```

首次启动会在当前目录生成 `config.ini`：默认 `provider=mock`（不联网）。要接入真实模型，
把 `[llm]` 改为 `provider=qianfan` 并填写 `api_key` / `model` / `search_model`（也可用
`QIANFAN_API_KEY` 等环境变量覆盖）。

其他入口：

```bash
python xAgent/main.py --list      # 列出全部工具（含已连接的 MCP 工具）
python xAgent/main.py --demo      # core 层文件工具烟雾测试
python xAgent/main.py --run is_leap_year year=2024
```

xAgent 的 18 个工具、`.mcp.json` 配置、AGENTS.md 注入、路径沙箱、调用日志与测试方式见
[xAgent/README.md](开发agent/Agent/xAgent/README.md)。

---

## 技术栈

- Python 3.10+
- MCP Python SDK（MCP Server / MCP Client）
- OpenAI 兼容 Chat Completions / 千帆智能搜索 API
- AnythingLLM Developer API
- Textual（终端 UI）、trafilatura（网页正文提取）
- HTTP / REST / Streamable HTTP / stdio

---

## 文档索引

- [宠物医院LLM 模块总览](宠物医院LLM/README.md)
- [宠物医院 MCP](宠物医院LLM/project/pet-hospital-mcp/README.md)
- [AnythingLLM MCP](宠物医院LLM/AnythingLLM%20MCP/README.md)
- [AnythingLLM Webpage](宠物医院LLM/AnythingLLM%20Sever/README.md)
- [xAgent](开发agent/Agent/xAgent/README.md)
- [Practice02](开发agent/Agent/Practice02/README.md)
- [Practice01](Agent/Practice01/README.md)
