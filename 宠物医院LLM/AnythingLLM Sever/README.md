# AnythingLLM Sever（网页上传）

单页网页：选择工作区、上传文档到 AnythingLLM 并触发向量化。

浏览器不直接访问 AnythingLLM，也不保存任何 API Key；所有请求都发到本地代理
`serve.py`，由代理从环境变量读取密钥后转发到 AnythingLLM Developer API。

## 启动

```powershell
$env:ANYTHINGLLM_API_KEY = "你的 Developer API Key"
$env:ANYTHINGLLM_BASE_URL = "http://localhost:3001/api"   # 可选，默认值即此
python serve.py
```

打开 http://127.0.0.1:3002/ 。

## 环境变量

| 变量 | 必需 | 默认值 | 说明 |
|---|---|---|---|
| `ANYTHINGLLM_API_KEY` | 是 | 无 | AnythingLLM Developer API Key，只存在于服务端环境变量 |
| `ANYTHINGLLM_BASE_URL` | 否 | `http://localhost:3001/api` | AnythingLLM API 地址 |
| `ANYTHINGLLM_TIMEOUT` | 否 | `60` | 请求上游超时（秒） |
| `WEB_HOST` | 否 | `127.0.0.1` | 本服务监听地址 |
| `WEB_PORT` | 否 | `3002` | 本服务监听端口 |

未配置 `ANYTHINGLLM_API_KEY` 时页面仍可打开，但接口返回 503 并提示配置缺失。

## 代理接口

| 方法 | 路径 | 转发到 |
|---|---|---|
| GET | `/api/workspaces` | `GET /api/v1/workspaces` |
| POST | `/api/upload` | `POST /api/v1/document/upload`（表单字段 `file`、`addToWorkspaces`） |

## 依赖与限制

- 仅使用 Python 标准库，无需安装第三方依赖。
- 单次上传请求体上限 64MB，超过时接口返回 413。
- 页面具备「加载中 / 成功 / 失败」三态提示；上传期间禁用文件与工作区选择。

## 安全提示

仓库历史提交中存在过一个明文 Developer API Key。删除代码不等于密钥失效，
请立即在 AnythingLLM 控制台吊销并重新生成密钥，同时注意历史提交仍可能保留旧密钥。
