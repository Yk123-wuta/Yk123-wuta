# Yk123-wuta

这是一个以 AI Agent、MCP（Model Context Protocol）和本地知识库/业务数据接入为主题的学习与实践项目仓库，主要用于探索如何将不同的数据源和工具能力接入到 AI Agent 中。

本仓库包含三个核心部分：

- 一个简易的聊天脚本示例
- 一个 AnythingLLM 的 MCP 适配层
- 一个宠物医院数据的 MCP 适配层

通过这些示例，学习如何把本地服务、知识库、数据库和 AI 模型连接起来，并让 Agent 以标准方式调用它们。

---

## 项目结构

```text
Yk123-wuta/
├── Agent/
│   └── Practice01/
│       ├── chat.py
│       ├── config.ini
│       └── README.md
├── LLM/
│   ├── README.md
│   ├── AnythingLLM MCP/
│   │   ├── opencode.json
│   │   ├── pyproject.toml
│   │   ├── src/
│   │   │   └── anythingllm_mcp/
│   │   └── tests/
│   ├── project/
│   │   ├── opencode.json
│   │   ├── pet-hospital-mcp-prompt.md
│   │   ├── pet-hospital-mcp/
│   │   └── pet-hospital-windows-amd64/
│   └── ...
└── README.md
```

---

## 1. Agent / Practice01

这个目录下是一个极简的聊天脚本示例，主要演示：

- 配置 OpenAI 兼容接口的参数
- 调用模型的 Chat Completions API
- 使用流式输出逐段返回文本

适合初学者快速理解“如何让 Python 程序调用大语言模型”。

相关文件：

- [Agent/Practice01/chat.py](Agent/Practice01/chat.py)
- [Agent/Practice01/config.ini](Agent/Practice01/config.ini)

---

## 2. AnythingLLM MCP

这是一个把 AnythingLLM 工作区能力封装为 MCP 工具的项目。

它的作用是：

- 读取配置中的 AnythingLLM API 地址和密钥
- 连接本地或远程的 AnythingLLM 实例
- 暴露工作区管理和问答能力
- 让 AI Agent 能通过标准的 MCP 调用方式访问知识库

主要能力包括：

- `list_workspaces`
- `get_workspace`
- `create_workspace`
- `update_workspace`
- `delete_workspace`
- `ask`

相关目录：

- [LLM/AnythingLLM MCP](LLM/AnythingLLM%20MCP)

---

## 3. 宠物医院 MCP

这个项目是本仓库中更贴近实际业务场景的部分，目标是将宠物医院的 REST API 封装成标准 MCP 工具，让 Agent 能直接查询宠物档案信息。

它的特点：

- 支持本地 stdio 传输
- 支持 Streamable HTTP 远程访问
- 通过 MCP 工具暴露查询能力
- 提供筛选、排序、分页等逻辑

当前主要工具：

- `get_meta`
- `list_pets`

它适合用来演示：

- 如何把传统 REST API 接入 MCP
- 如何让 Agent 通过标准协议访问业务数据
- 如何在 AI 智能体中接入真实业务系统

相关目录：

- [LLM/project/pet-hospital-mcp](LLM/project/pet-hospital-mcp)
- [LLM/project/pet-hospital-mcp-prompt.md](LLM/project/pet-hospital-mcp-prompt.md)

---

## 运行方式

### 1. 运行聊天脚本

先在 [Agent/Practice01/config.ini](Agent/Practice01/config.ini) 中填写模型地址和密钥，然后执行：

```bash
cd Agent/Practice01
python chat.py
```

---

### 2. 启动 AnythingLLM MCP

进入项目目录：

```bash
cd "LLM/AnythingLLM MCP"
pip install -e .
```

然后按实际环境启动：

```bash
python -m anythingllm_mcp.server
```

或通过配置文件接入到 OpenCode / MCP 客户端。

---

### 3. 启动宠物医院 MCP

进入目录：

```bash
cd "LLM/project/pet-hospital-mcp"
pip install -e .
```

本地 stdio 方式：

```bash
python -m hospital_mcp.server stdio
```

HTTP 方式：

```bash
python -m hospital_mcp.server http --port 8301
```

---

## 适合的学习目标

这个项目特别适合用于以下几类学习：

1. AI Agent 基础接入
2. MCP 协议入门与实践
3. 把大型模型接到真实数据源
4. 把本地知识库和业务系统封装成标准工具
5. 从 demo 逐步走向真实生产能力

---

## 技术栈

- Python
- OpenAI 兼容 SDK
- MCP（Model Context Protocol）
- AnythingLLM
- HTTP / REST API
- OpenCode 作为 MCP 客户端配置示例

---

## 备注

这个仓库更偏向“学习型项目”和“实践型项目”，而不是一个正式的生产级应用。它的目的是帮助我理解：

- AI 如何访问外部数据
- MCP 如何统一不同工具的访问方式
- Agent 如何更自然地调用知识库和业务系统

如果你在继续开发这个项目，可以把它逐步扩展成：

- 更完整的宠物医院数据查询工具集
- 更强的 AnythingLLM 工作区管理能力
- 更稳定的 Agent 工作流
- 更完善的 README 和文档说明

---

## 结语

这个仓库记录了我在 AI Agent 与 MCP 方向上的探索与实践，适合用于学习、原型开发以及后续功能扩展。

如果你想继续深入，可以从以下顺序入手：

1. 先理解 [Agent/Practice01/chat.py](Agent/Practice01/chat.py)
2. 再看 [LLM/AnythingLLM MCP](LLM/AnythingLLM%20MCP)
3. 最后研究 [LLM/project/pet-hospital-mcp](LLM/project/pet-hospital-mcp)

这样会更容易理解整个项目的设计思路。
