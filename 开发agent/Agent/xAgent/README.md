# xAgent — 文件操作 Agent（Core + TUI）

xAgent 是一个可直接运行的文件操作 Agent。它在 `Practice02/chat-stream-tool-fileops.py` 原型的基础上重构为
**Core / UI 分离**的分层结构，并补齐了模型工具调用循环、技能机制、MCP 客户端、AGENTS.md 注入、
会话持久化、上下文压缩、路径沙箱与调用日志。

## 目录结构

```text
xAgent/
├── core/                    纯逻辑层（零 UI 依赖）
│   ├── types.py             ToolResult / ToolInfo 数据契约
│   ├── tools.py             文件/时间/联网/CLI 工具纯函数（标准库 + curl + trafilatura）
│   ├── registry.py          工具注册表、参数转换、MCP 工具动态注册、ToolService 门面
│   ├── config.py            config.ini 读取、环境变量覆盖、AGENTS.md 读取与截断
│   ├── llm.py               LLM 适配层（qianfan / mock）与 tool_calls 解析
│   ├── skills.py            Skill 发现、加载、创建（YAML frontmatter）
│   ├── mcp_client.py        MCP 客户端（Streamable HTTP / stdio）、退避重连
│   ├── context.py           上下文统计、压缩摘要、归档与 recallContext 检索
│   ├── sessions.py          会话 JSONL 持久化、列表与恢复
│   ├── call_log.py          工具调用日志落盘（JSONL）
│   └── __init__.py
├── tui/                     Textual 终端界面（仅依赖 core）
│   └── app.py
├── tests/                   单元测试（5 个测试文件）
├── main.py                  统一 CLI 入口
├── requirements.txt
├── __init__.py / __main__.py
```

## 分层架构

```text
┌───────────────────────────────────────────────┐
│ tui/app.py（Textual）  ← 可替换为 web/desktop  │
└───────────────────────┬───────────────────────┘
                        │ 只调用 ToolService.call / list_tools
┌───────────────────────▼───────────────────────┐
│ core/registry.py  ToolService 门面             │
│ 参数转换 · 异常包装 · 调用日志 · MCP 工具注册   │
└───────────────────────┬───────────────────────┘
                        │
┌───────────────────────▼───────────────────────┐
│ core/tools.py  18 个本地工具纯函数             │
│ core/llm.py · skills.py · mcp_client.py ·      │
│ context.py · sessions.py                       │
└───────────────────────────────────────────────┘
```

- UI 层只 import `core.ToolService`，不直接调用 `tools.py`。
- `call_tool()` / `TOOLS` 保留为兼容入口，返回 dict；`ToolService` 返回 `ToolResult`。

## 快速开始

要求 Python 3.10+；`fetchWeb` 还需要系统 `curl` 在 PATH 中。

```bash
cd 开发agent/Agent
pip install -r xAgent/requirements.txt
python xAgent/main.py          # 默认启动 Textual TUI
```

> **工作目录即项目根目录**。xAgent 以启动时的当前工作目录为准：
> 生成并读取 `config.ini`、扫描 `.agents/skills/`、读写 `.agents/sessions/`、
> `.agents/context/`、`.agents/logs/`，并读取 `AGENTS.md`。请始终从同一个目录启动。

首次启动会写出 `config.ini`，默认 `provider=mock`（本地演示，不联网）。接入真实模型时修改
`[llm] provider=qianfan` 并填写 `api_key`、`model`、`search_model`，然后重启。

其他入口：

```bash
python xAgent/main.py --demo      # core 层文件工具烟雾测试（临时目录 + 沙箱）
python xAgent/main.py --list      # 列出工具（会尝试连接 MCP Server）
python xAgent/main.py --run get_current_datetime
python xAgent/main.py --run is_leap_year year=2024
python xAgent/main.py --run create_file file_path=notes.txt content=hello
```

## 18 个本地工具

| # | 工具 | 说明 | 主要参数 |
|---|---|---|---|
| 1 | `get_current_working_directory` | 返回进程真实工作目录 | — |
| 2 | `get_current_datetime` | 本地时间 / UTC / 时区 / 时间戳（读系统时钟） | — |
| 3 | `is_leap_year` | 闰年判断（4 年规则 + 整百年 400 规则） | `year`（1-9999，必需） |
| 4 | `list_directory` | 枚举目录中的文件与子目录 | `path` |
| 5 | `get_file_type` | 文件类型 / MIME / 大小 | `file_path` |
| 6 | `read_file` | 读取文本文件（二进制拒绝） | `file_path` |
| 7 | `create_file` | 创建文件（已存在则拒绝） | `file_path`、`content` |
| 8 | `write_file` | 覆盖 / 追加写入文本 | `file_path`、`content`、`mode` |
| 9 | `create_directory` | 创建目录（自动级联父目录） | `dir_path` |
| 10 | `delete_directory` | 删除目录（非空需 `force=true`） | `dir_path`、`force` |
| 11 | `delete_file` | 删除文件 | `file_path` |
| 12 | `rename_item` | 重命名文件/目录（拒绝覆盖） | `source_path`、`new_name` |
| 13 | `searchWeb` | 调用千帆智能搜索生成 API，返回摘要与参考来源 | `query`、`top_k`（1-10） |
| 14 | `fetchWeb` | curl 抓取网页 + trafilatura 提取正文为 Markdown | `url` |
| 15 | `runCli` | 执行本地命令行程序，返回 stdout/stderr/退出码 | `command`、`args`、`timeout` |
| 16 | `loadSkill` | 加载 Skill 的 SKILL.md 或 Skill 目录内附加文件 | `name`、`filename` |
| 17 | `createSkill` | 在 `.agents/skills/` 下创建新 Skill | `name`、`skillContext` |
| 18 | `recallContext` | 按关键词检索上下文归档 | `text`、`id` |

> 以上是注册表中的**本地工具**。连接 MCP Server 后，外部工具会以动态工具的形式追加到
> 可调用集合中（详见下文），因此 `--list` 的实际条数可能大于 18。

## 配置项（config.ini）

首次运行写出的完整配置包含以下内容，所有值均可省略并使用默认值：

| 段 | 键 | 默认值 | 说明 |
|---|---|---|---|
| `[llm]` | `provider` | `mock` | `mock`（不联网）/ `qianfan`（OpenAI 兼容接口） |
| | `api_key` | `mock-key-please-replace` | 真实 Key 请本地填写，勿提交 |
| | `base_url` | `https://qianfan.baidubce.com/v2` | 模型接口地址 |
| | `model` | `mock-model` | 对话模型 |
| | `search_model` | `mock-model` | `searchWeb` 使用的搜索模型 |
| | `search_url` | 千帆 `ai_search/chat/completions` | 搜索接口地址 |
| | `max_model_calls` | `8` | 单轮对话最多请求模型的次数 |
| `[context]` | `window` | `0` | 0 = 自动推断；识别不到时按 128k 处理 |
| | `warn_ratio` | `0.70` | 上下文占用达到该比例时暂停输入并压缩 |
| | `summary_target_tokens` | `5000` | 压缩摘要目标 Token |
| | `archive_dir` | `.agents/context` | 压缩前原文归档目录 |
| | `recall_max_matches` / `recall_snippet_chars` / `recall_total_chars` | `8` / `240` / `6000` | recallContext 检索条数与长度上限 |
| | `tool_result_limit` | `2000` | 回填给模型的工具结果字符上限 |
| `[session]` | `dir` | `.agents/sessions` | 会话 JSONL 目录 |
| | `agents_md` | `AGENTS.md` | 项目规则文件名（可换等价命名） |
| | `agents_md_max_chars` | `8000` | AGENTS.md 注入前最大字符数，超长截断 |
| `[skills]` | `max_chars` | `12000` | `loadSkill` 返回正文的字符上限 |
| `[mcp]` | `config_file` | `.mcp.json` | MCP Server 配置文件 |
| | `connect_timeout` | `15` | 单次连接超时（秒） |
| | `call_timeout` | `120` | 单次工具调用超时（秒） |
| | `reconnect_delays` | `1,2,5,10,30` | 断线重连退避（秒），用完后重复最后一项 |
| `[tools]` | `sandbox_root` | 空 | 文件操作沙箱根目录；空 = 当前工作目录 |
| | `search_top_k` | `10` | 搜索返回条数默认值 |
| | `search_timeout` | `60` | 搜索超时（秒） |
| | `fetch_timeout` / `fetch_connect_timeout` | `30` / `10` | `fetchWeb` 的 curl 超时 |
| | `fetch_max_chars` | `20000` | 网页正文返回上限 |
| | `cli_timeout` / `cli_max_timeout` | `30` / `120` | `runCli` 默认/最大超时 |
| | `cli_max_output_chars` | `8000` | `runCli` 输出截断长度 |
| `[logging]` | `enabled` | `true` | 工具调用日志开关 |
| | `dir` | `.agents/logs` | 日志目录 |
| | `arg_chars` / `result_chars` | `200` / `400` | 入参/结果摘要长度 |
| `[ui]` | `sidebar_width` | `40` | TUI 侧栏宽度 |

支持的环境变量（优先级高于 `config.ini`）：

| 变量 | 对应配置 |
|---|---|
| `XAGENT_PROVIDER` | `[llm] provider` |
| `QIANFAN_API_KEY` | `[llm] api_key` |
| `QIANFAN_BASE_URL` | `[llm] base_url` |
| `QIANFAN_MODEL` | `[llm] model` |
| `QIANFAN_SEARCH_MODEL` | `[llm] search_model` |
| `QIANFAN_SEARCH_URL` | `[llm] search_url` |
| `XAGENT_AGENTS_MD` | `[session] agents_md` |
| `XAGENT_MCP_CONFIG` | `[mcp] config_file` |
| `XAGENT_SANDBOX_ROOT` | `[tools] sandbox_root` |
| `XAGENT_LOG_ENABLED` | `[logging] enabled`（`1/true/yes/on` 开，`0/false/no/off` 关） |
| `XAGENT_LOG_DIR` | `[logging] dir` |

## MCP 客户端

xAgent 从**当前工作目录**读取 `.mcp.json`，连接其中 `mcpServers` 声明的 Server：

```json
{
  "mcpServers": {
    "pet-hospital": {
      "type": "stdio",
      "command": "pet-hospital-mcp",
      "args": ["stdio"],
      "env": { "PET_HOSPITAL_URL": "http://127.0.0.1:8080" }
    },
    "anythingllm": {
      "type": "streamable-http",
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

- 传输方式：`stdio`（启动本地子进程，绑定 stdin/stdout）与 Streamable HTTP
  （`type`/`transport` 取 `http`、`streamable-http`、`streamable_http` 均可）。
  HTTP 条目支持 `url`（也接受 `httpUrl` / `http_url`）与 `headers`；
  stdio 条目支持 `command`、`args`、`env`、`cwd`。
- 连接成功后调用 `list_tools()`，把外部工具转换为 Function Calling schema 并注册；
  与本地工具重名时使用 `<server>__<tool>` 前缀隔离，内部记录来源 Server。
- 连接失败不会阻塞主流程：警告写入 TUI 状态栏，后台按 `reconnect_delays` 退避重连；
  断开时注销该 Server 的工具，恢复后重新注册。
- 所有 MCP 工具调用同样经过 `ToolService`，因此也会写入调用日志。

## AGENTS.md 注入

- 从当前工作目录读取 `AGENTS.md`（文件名可用 `[session] agents_md` 或 `XAGENT_AGENTS_MD` 指定，
  兼容等价命名）。
- 会话首次加载时作为一条 System 消息注入，内容以 `[项目规范] AGENTS.md` 标记开头，只注入一次。
- 文件缺失、不可读或为空时静默跳过，不报错。
- 超过 `agents_md_max_chars`（默认 8000 字符）时截断，并在内容末尾追加截断说明，避免占满上下文。
- 恢复历史会话时，如果存档中已有该 System 消息，则直接复用，保持行为一致。

## 技能（Skill）

- 启动时扫描 `<工作目录>/.agents/skills/<skill>/SKILL.md`，解析 YAML frontmatter 的 `name` 与 `description`。
- 只把名称和描述注入 System Prompt；命中技能时要求模型先调用 `loadSkill` 读取正文（渐进加载）。
- `loadSkill` 只允许读取 Skill 目录内的文件（绝对路径、相对路径、仅文件名递归搜索都做目录边界校验）；
  返回正文超过 `[skills] max_chars`（默认 12000 字符）时截断并附带警告。
- `createSkill` 校验名称为小写字母/数字/连字符，自动补齐 frontmatter；目标已存在时拒绝覆盖。
- 没有注册任何 Skill 时，不注入技能清单。

## 路径沙箱与安全约束

- 所有文件/目录工具（读、写、创建、删除、重命名、列目录、取类型）统一经过沙箱校验：
  解析后的真实路径必须位于 `[tools] sandbox_root` 内，默认是当前工作目录；越界直接返回
  `路径越界` 错误，不执行任何 IO。
- 不能删除当前工作目录或其祖先目录。
- `rename_item` 拒绝包含路径分隔符的新名称，目标已存在时拒绝覆盖。
- `read_file` 拒绝二进制内容；`fetchWeb` 使用参数列表调用 curl，不经过 shell。
- `runCli` 可执行任意本地命令，**没有命令白名单或确认环节**，属于已知设计风险，请勿在
  不可信输入环境中开放该工具。

## 工具调用日志

- 每次 `ToolService.call` / `call_tool` 都会向 `<log_dir>/tool_calls-YYYYMMDD.jsonl` 追加一行 JSON：
  `time`、`tool`、`success`、`elapsed_ms`、`arguments`、`result`（后两者按配置长度截断）。
- 默认目录 `.agents/logs`，可通过 `[logging] dir` 或 `XAGENT_LOG_DIR` 修改；
  `[logging] enabled=false` 或 `XAGENT_LOG_ENABLED=0` 可关闭。
- 日志写入失败会被忽略，不影响工具调用结果；TUI 中的耗时卡片与落盘日志相互独立。

## 会话、上下文与检索

- 会话以 JSONL 保存在 `.agents/sessions/`，支持列表、加载与切换；恢复时保留 System Prompt 与 AGENTS.md。
- 上下文占用达到 `warn_ratio`（默认 70%）时暂停新输入，先归档原文到 `.agents/context/`，
  再生成约 `summary_target_tokens` 的摘要替换历史消息，保留 System Prompt 与工具定义。
- 压缩摘要末尾注明归档时间与编号；模型可用 `recallContext` 按关键词回到原始归档片段。
- TUI 顶部/侧栏显示当前上下文长度、窗口大小与占用百分比；无法识别窗口时按 128k 并标注默认。

## LLM 适配

- `provider=mock` 或无 API Key 时使用本地 Mock 回复，不会发起网络请求。
- `provider=qianfan` 走 OpenAI 兼容的 Chat Completions 接口，支持流式输出与 `tool_calls` 解析。
- 单轮对话最多 `max_model_calls`（默认 8）次模型调用；工具失败以 `success=false` + `error`
  回填给模型，模型可自行决定下一步。

## 测试

```bash
cd 开发agent/Agent
python -m unittest discover -s xAgent/tests -v

# Practice02 回归测试
cd Practice02 && python -m unittest test_fileops -v
```

## 已知限制

- `searchWeb` 依赖真实 `[llm] provider=qianfan` 与 API Key，Mock 模式会返回配置缺失提示。
- `fetchWeb` 依赖系统 `curl` 与 `trafilatura`，缺失时返回明确错误。
- MCP Server 在 xAgent 启动时连接；若中途启动或重启 MCP Server，需要重启 xAgent 或等待后台退避重连。
