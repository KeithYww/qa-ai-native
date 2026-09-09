# QuAIA™ — 基于智能体的质量保障框架

<img src="static/quaia_logo.png" alt="QuAIA Logo" width="50" style="margin-right: 15px; float:left">

QuAIA™ 是一个开源框架,用于智能化自动执行软件测试生命周期中最重要的环节——从软件需求评审一直到生成测试执行报告。

对应的 Medium 文章可以在
[这里](https://medium.com/@partarstu/the-next-evolution-in-software-testing-from-automation-to-autonomy-1bd7767802e1)找到。

## 演示

观看 QuAIA™ 的实际运行演示:

[QuAIA™ Framework Demo](https://youtu.be/LUf6ydlKfIU)

## 功能特性

* **模块化智能体架构:** 包含以下专用智能体:
    * 需求评审(Requirements Review)
    * 测试用例生成(Test Case Generation)
    * 测试用例分类(Test Case Classification)
    * 测试用例评审(Test Case Review)
    * UI 与 API 测试执行(独立项目)
    * 缺陷报告创建(Incident Report Creation)
* **Photon API Relay:** 所有 LLM 调用均通过内部 Anthropic 兼容代理(`PHOTON_API_BASE_URL`)路由,支持 Claude、Qwen、DeepSeek、Kimi 等模型,API 错误时自动切换到备用模型。
* **飞书(Feishu / Lark)集成:**
    * **飞书 MCP Server**(`scripts/feishu_mcp_server.py`)——从飞书读取 PRD Wiki 和 Docx 文档,以 MCP 工具的形式暴露给智能体。
    * **飞书项目(Meego)回写**——生成的测试用例将作为 `test_cases` 工作项创建到飞书项目空间中,评审智能体会为其添加评论和状态流转。
* **Jira 集成:** 缺陷创建阶段的重复检测仍通过 Jira MCP Server 使用 Jira;RAG 同步仍通过 REST API 读取 Jira issue。
* **Jira RAG 同步:** 以编程方式(通过 orchestrator 的 `/update-rag-db` 端点触发)使 Qdrant 向量存储与项目的 Jira issue 保持同步,无需调用 LLM 智能体。
* **专用 Prompt Guard 服务:** 一个专用微服务,使用 ProtectAI 模型检测提示词注入攻击。
* **Web UI 监控仪表盘:** 实时监控界面,支持:
    * 智能体状态可视化(AVAILABLE、BUSY、BROKEN 状态)
    * 带执行细节和耗时的任务历史记录
    * 支持筛选的错误日志查看器
    * 按任务查看智能体执行日志
    * 汇总统计信息(运行时长、任务数、智能体健康状况)
* **智能体状态管理:** 智能的智能体生命周期管理,包括:
    * 自动状态跟踪(AVAILABLE → BUSY → AVAILABLE/BROKEN)
    * 带原因分类的故障智能体检测(OFFLINE、TASK_STUCK)
    * 支持任务取消的自动恢复机制
    * 通过原子化预留实现并发安全的智能体选择
* **智能体日志采集:** 采集智能体的执行日志并以 artifact 形式返回,用于调试和监控。
* **JWT 认证:** 通过可配置的凭据和令牌有效期,保障仪表盘访问的安全性。
* **提示词注入防护:** 内置安全机制,用于检测和防止提示词注入攻击。
* **兼容 A2A 与 MCP:** 遵循 Agent2Agent 和 Model Context 协议的规范。
* **编排层:** 中央 orchestrator 负责管理智能体注册、任务路由和工作流执行。
* **向量数据库集成:** 使用 Qdrant 实现语义搜索能力,支持智能重复检测和基于 RAG 的功能。
* **Embedding 服务:** 专用微服务,使用 SentenceTransformer 模型生成文本 embedding。
* **测试管理系统集成:** 集成飞书项目(Meego)、Zephyr 和 Xray,用于测试用例管理相关操作。
* **测试报告:** 为测试执行结果生成详细的 Allure 报告。
* **可扩展性:** 设计上便于新增智能体、工具和集成。
* **架构即代码(CALM):** 系统架构(包括其安全控制措施)使用 [FINOS CALM](https://calm.finos.org/) 标准描述,并作为阻塞式 CI 关卡进行校验,确保模型与运行中的系统保持同步。

## 架构

Orchestrator 作为中央枢纽,管理各个专用智能体的生命周期和交互。智能体会向
orchestrator 公开其能力详情,使其能够识别自己可以处理的任务。

当事件发生时(例如,飞书项目 webhook 表示某个 story 已可进行需求评审或测试用例生成),orchestrator 会:

1. 接收事件。
2. 根据任务描述和已注册智能体的能力,识别合适的智能体。
3. 将任务路由到选定的智能体。
4. 监控任务执行并收集结果。
5. 根据需要触发后续智能体或工作流(例如,测试用例生成后触发测试用例
   分类)。

### 智能体状态管理

Orchestrator 为每个已注册的智能体维护详细的状态信息:

| 状态 | 描述 |
|--------|-------------|
| **AVAILABLE** | 智能体已就绪,可以接受新任务。 |
| **BUSY** | 智能体正在执行任务。 |
| **BROKEN** | 智能体因 OFFLINE 或存在 TASK_STUCK 而不可用。 |

**故障智能体分类:**
- `OFFLINE`: 智能体对健康检查无响应。
- `TASK_STUCK`: 智能体有响应,但任务已超时。

### 并发与智能体选择

Orchestrator 使用基于锁的原子化智能体选择机制,防止多个任务
争用同一批可用智能体时发生竞态条件。主要特性包括:

* **原子化预留:** 智能体选择和状态更新在同一个锁内完成,防止重复分配。
* **等待并重试:** 如果没有合适的智能体可用,orchestrator 会以指数退避方式等待。
* **基于 LLM 的选择缓存:** Orchestrator 会为智能体集合缓存 LLM 决策,避免重复的 API 调用。

### 故障智能体恢复

一个后台任务持续监控故障智能体并尝试恢复:

1. 对于 `OFFLINE` 的智能体:定期检查该智能体是否响应 card 获取请求。
2. 对于 `TASK_STUCK` 的智能体:在将该智能体标记为可用之前,尝试使用 A2A 协议取消卡住的任务。
3. 对于持续 24 小时仍无法恢复的智能体,将放弃恢复。

有关系统架构和数据流的可视化表示,请参考以下图表:

* [架构图](architectural_diagram.html)([德语版](architectural_diagram_DE.html))
* [流程图](flow_diagram.html)([德语版](flow_diagram_DE.html))

### 架构即代码(CALM)

上述架构还使用 [FINOS CALM](https://calm.finos.org/)(Common Architecture Language Model)标准,以机器可读的
**架构即代码** 形式维护,位于 [`calm/`](calm/) 目录下。
这使得该架构成为一等的、受版本控制的产物,而不是会逐渐过时的静态图表。

该模型将每个服务和外部系统捕获为 `nodes`,它们之间的集成边(A2A、MCP、HTTPS)捕获为
`relationships`,并将框架的安全机制作为 `controls` 附加到相关的节点和边上:

| 控制措施 | 适用对象 | 机制 |
|---|---|---|
| Orchestrator API key | Orchestrator | 控制/webhook 端点上的 `X-API-Key`(`ORCHESTRATOR_API_KEY`) |
| Dashboard JWT | Orchestrator | 仪表盘端点上的 JWT(`DASHBOARD_JWT_SECRET`) |
| 执行智能体 bearer token | Orchestrator → 执行智能体 | `Authorization: Bearer` 头(`REMOTE_EXECUTION_AGENT_AUTH_TOKEN`) |
| 提示词注入防护 | 每个智能体 | 提示词注入检测(`PROMPT_INJECTION_CHECK_ENABLED`) |
| 内部服务 API key | Embedding 与 Prompt Guard 服务 | 共享的 `X-API-Key`(`INTERNAL_SERVICE_API_KEY`) |

有一条集成边故意没有鉴权控制:飞书项目原生的"发送 HTTP 请求"自动化动作(调用 orchestrator 的
`/requirement-ready-for-review` webhook)无法附带自定义 header,因此该端点按设计不鉴权。滥用风险
改由内存中的按工作项去重窗口 + 全局提交速率上限来控制(`REQUIREMENT_REVIEW_DEDUP_WINDOW_SECONDS` /
`REQUIREMENT_REVIEW_MAX_PER_MINUTE`)。

治理**模式(pattern)**(`calm/patterns/quaia.pattern.json`)用于断言每个必需的节点、关系和控制措施
均已存在。CI 流水线将此校验作为**阻塞式**的 `Architecture (CALM)` 任务运行,因此移除某个智能体或
放弃某项安全控制措施都会导致构建失败。完整的目录结构以及如何在本地运行校验,请参见
[`calm/README.md`](calm/README.md)(需要 Node.js 20+)。

## 快速开始

### 前置条件

* Python 3.14+
* Docker
* [`uv`](https://docs.astral.sh/uv/)(Python 包与项目管理工具)
* [Node.js](https://nodejs.org/) 20+(仅在需要本地校验 CALM 架构模型时才需要;参见
  [架构即代码(CALM)](#架构即代码calm))

### 安装配置

1. **克隆代码仓库:**
   ```bash
   git clone https://github.com/partarstu/agentic-qa-framework.git
   cd agentic-qa-framework
   ```

2. **安装 `uv`**(如尚未安装):
   ```bash
   # macOS / Linux
   curl -LsSf https://astral.sh/uv/install.sh | sh
   # Windows (PowerShell)
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

3. **创建虚拟环境并安装依赖:**
   ```bash
   uv sync
   ```
   此命令会创建一个 `.venv` 目录,并安装锁定版本的运行时和开发
   依赖。使用 `uv run` 在该环境内运行命令,例如
   `uv run pytest`。embedding 服务和 prompt-guard 服务的可选机器学习依赖会通过
   `uv sync --extra embedding-service` 或 `uv sync --extra prompt-guard-service` 按需安装。

### Docker 镜像

该项目使用 Docker 对 orchestrator 和各智能体服务进行容器化。一个通用基础镜像 `agentic-qa-base:latest` 由 `Dockerfile.base` 构建而来,以确保一致性并缩短构建时间。

每个服务均使用 `gunicorn` 作为 WSGI 服务器运行。智能体使用的命令是
`gunicorn -w 1 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT agents.<agent_name>.main:app`,orchestrator
使用的命令是 `gunicorn -w 1 -k uvicorn.workers.UvicornWorker orchestrator.main:orchestrator_app`。请注意,
`$PORT` 指的是该智能体在内部监听的端口,而 `AgentCard` 的 URL 会使用 `EXTERNAL_PORT`。

### 环境变量

在项目根目录下创建一个 `.env` 文件,并配置以下环境变量。这些变量控制
orchestrator 和各智能体的行为。

```
# 日志
LOG_LEVEL=INFO
GOOGLE_CLOUD_LOGGING_ENABLED=False

# Orchestrator
ORCHESTRATOR_HOST=localhost
ORCHESTRATOR_PORT=8000
ORCHESTRATOR_URL=http://localhost:8000
ORCHESTRATOR_API_KEY=YOUR_ORCHESTRATOR_API_KEY # 必需。未设置时控制/webhook 端点返回 HTTP 503。
                                 # /requirement-ready-for-review 不受此限制——见下方 REQUIREMENT_REVIEW_*。
REQUIREMENT_REVIEW_DEDUP_WINDOW_SECONDS=300 # 默认 300。不鉴权的 /requirement-ready-for-review webhook 的按工作项去重窗口(秒)。
REQUIREMENT_REVIEW_MAX_PER_MINUTE=5 # 默认 5。/requirement-ready-for-review 的全局提交速率上限(每分钟)。
JIRA_MCP_SERVER_URL=http://localhost:9000/sse
FEISHU_MCP_SERVER_URL=http://localhost:9010/sse

# Photon API Relay(内部 LLM 代理,兼容 Anthropic 协议)
PHOTON_API_BASE_URL=https://coding.corp.photontech.cc
PHOTON_API_KEY=YOUR_PHOTON_API_KEY # 必需。

# 飞书(Lark)应用凭据——飞书 MCP Server 用于读取 PRD 文档
FEISHU_APP_ID=YOUR_FEISHU_APP_ID
FEISHU_APP_SECRET=YOUR_FEISHU_APP_SECRET

# 飞书项目(Meego)——当 TEST_MANAGEMENT_SYSTEM=meego 时使用
MEEGO_BASE_URL=https://project.feishu.cn
MEEGO_PLUGIN_ID=YOUR_MEEGO_PLUGIN_ID # 必需,用于测试用例回写到飞书项目。
MEEGO_PLUGIN_SECRET=YOUR_MEEGO_PLUGIN_SECRET # 必需。
MEEGO_PROJECT_KEY=union-platform
MEEGO_USER_KEY= # 可选。

# 仪表盘认证
DASHBOARD_USERNAME=admin # 必需。未设置时认证失败关闭。
DASHBOARD_PASSWORD=admin # 必需。生产环境请务必修改!
DASHBOARD_JWT_SECRET=change-me-in-production-please # 必需。生产环境请务必修改!
DASHBOARD_JWT_EXPIRE_HOURS=24

# Zephyr 测试管理系统(当 TEST_MANAGEMENT_SYSTEM=zephyr 时使用)
ZEPHYR_BASE_URL=YOUR_ZEPHYR_BASE_URL
ZEPHYR_API_TOKEN=YOUR_ZEPHYR_API_TOKEN

# 智能体配置
AGENT_BASE_URL=http://localhost
PORT=8001
EXTERNAL_PORT=8001

# 智能体发现
REMOTE_EXECUTION_AGENT_HOSTS=http://localhost
AGENT_DISCOVERY_PORTS=8001-8007
REMOTE_EXECUTION_AGENT_AUTH_TOKEN= # 可选。执行智能体的共享 bearer token。

# 任务执行
TASK_EXECUTION_TIMEOUT=1200 # 默认 1200 秒(20 分钟)。生成→分类→评审全流程最大等待时间。

# Google Cloud Storage(通过卷挂载)
ATTACHMENTS_LOCAL_DESTINATION_FOLDER_PATH=/tmp
MCP_SERVER_ATTACHMENTS_FOLDER_PATH=/tmp
JIRA_ATTACHMENT_SKIP_POSTFIX=_SKIP

# OpenTelemetry(链路追踪,留空则禁用)
OTEL_EXPORTER_OTLP_ENDPOINT= # 可选。OTLP HTTP 端点,例如 http://tempo:4318。

# 测试管理系统
TEST_MANAGEMENT_SYSTEM=meego # 可选值: meego(飞书项目)、zephyr、xray。默认: zephyr。

# 测试报告
TEST_REPORTER=allure
ALLURE_RESULTS_DIR=allure-results
ALLURE_REPORT_DIR=allure-report

# 通用模型配置
TOP_P=1.0
TEMPERATURE=0.0

# Qdrant 向量数据库
QDRANT_URL=http://localhost
QDRANT_PORT=6333
QDRANT_API_KEY= # 可选。
QDRANT_COLLECTION_NAME=jira_issues
QDRANT_METADATA_COLLECTION_NAME=rag_metadata
RAG_MIN_SIMILARITY_SCORE=0.7
RAG_MAX_RESULTS=5
RAG_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
EMBEDDING_SERVICE_URL= # 使用向量数据库的智能体必需。
EMBEDDING_SERVICE_TIMEOUT_SECONDS=120.0
EMBEDDING_SERVICE_MAX_RETRIES=6
EMBEDDING_SERVICE_RETRY_BACKOFF_CAP_SECONDS=32.0

# 缺陷创建智能体
INCIDENT_AGENT_MIN_SIMILARITY_SCORE=0.7
ISSUE_PRIORITY_FIELD_ID=priority
ISSUE_SEVERITY_FIELD_NAME=customfield_10124

# 提示词注入检测
PROMPT_INJECTION_CHECK_ENABLED=True
PROMPT_GUARD_PROVIDER=protect_ai
PROMPT_GUARD_SERVICE_URL= # PROMPT_INJECTION_CHECK_ENABLED=True 时必需。
INTERNAL_SERVICE_API_KEY= # 可选。embedding 和 prompt-guard 服务的共享密钥。
PROMPT_INJECTION_MIN_SCORE=0.8
PROMPT_INJECTION_MODEL_NAME=ProtectAI/deberta-v3-base-prompt-injection-v2

**关于本地模型的说明:**
如果你在本地(而非云端 Docker 容器)运行 orchestrator 或智能体,需手动下载所需模型:
1. **提示词注入检测模型:** `PROMPT_INJECTION_CHECK_ENABLED=True` 时需要。运行 `scripts/download_prompt_guard_model.py`。
2. **Embedding 模型:** 缺陷创建智能体和 orchestrator 需要。运行 `scripts/download_embedding_model.py`。
```

### Jira MCP Server 设置

QuAIA™ 框架通过一个 Model Context Protocol(MCP)server 与 Jira 集成。

1. **创建 `.env` 文件:**
   在 `mcp/jira/` 目录下创建 `.env` 文件:

   ```
   JIRA_URL=YOUR_JIRA_INSTANCE_URL
   JIRA_API_TOKEN=YOUR_JIRA_API_TOKEN
   JIRA_USERNAME=YOUR_JIRA_USERNAME
   ```

2. **使用 Docker 运行:**
   ```bash
   cd mcp/jira
   start_mcp_server.bat
   ```
   在云端部署中,将 GCS bucket 挂载到容器以便智能体访问下载的附件。

### 飞书 MCP Server 设置

飞书 MCP Server 读取飞书 Wiki 和 Docx 格式的 PRD 文档,以 MCP 工具的形式暴露给测试用例生成和评审智能体。

1. **在 `.env` 中配置凭据:**
   ```
   FEISHU_APP_ID=YOUR_FEISHU_APP_ID
   FEISHU_APP_SECRET=YOUR_FEISHU_APP_SECRET
   FEISHU_MCP_SERVER_URL=http://localhost:9010/sse
   ```

2. **启动飞书 MCP Server:**
   ```bash
   uv run gunicorn -w 1 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:9010 scripts.feishu_mcp_server:app
   ```
   Server 默认监听 `9010` 端口,自动解析 Wiki token URL(`/wiki/TOKEN`)和 Docx URL。
   `scripts/Dockerfile` 提供了容器化部署的 Docker 镜像。

### 在本地启动智能体

1. **启动 Qdrant 向量数据库(RAG 功能所需):**
   ```bash
   scripts/start_qdrant.bat
   ```
   在端口 6333 以 Docker 容器方式启动 Qdrant。

2. **启动飞书 MCP Server(需求评审、测试用例生成/评审所需):**
   ```bash
   uv run gunicorn -w 1 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:9010 scripts.feishu_mcp_server:app
   ```

3. **启动 Jira MCP Server(缺陷创建所需):**
   参见上文 [Jira MCP Server 设置](#jira-mcp-server-设置)。

4. **启动 Embedding 服务(可选):**
   ```bash
   uv run python services/embedding_service/main.py
   ```

5. **启动 Prompt Guard 服务(可选):**
   如果启用了提示词注入检测,则此项为必需。
   ```bash
   uv run python services/prompt_guard_service/main.py
   ```

6. **启动各个智能体:**
   为每个智能体打开单独的终端窗口:

    * **需求评审智能体:**
      ```bash
      uv run python agents/requirements_review/main.py
      ```
    * **测试用例生成智能体:**
      ```bash
      uv run python agents/test_case_generation/main.py
      ```
    * **测试用例分类智能体:**
      ```bash
      uv run python agents/test_case_classification/main.py
      ```
    * **测试用例评审智能体:**
      ```bash
      uv run python agents/test_case_review/main.py
      ```
    * **缺陷创建智能体:**
      ```bash
      uv run python agents/incident_creation/main.py
      ```

7. **启动 Orchestrator:**
   ```bash
   uv run python orchestrator/main.py
   ```

### Web UI 监控仪表盘

Orchestrator 内置了一个 Web UI,用于实时监控智能体状态、任务和日志。

#### 访问仪表盘

orchestrator 运行后,访问 `http://localhost:8000/`(或你配置的 orchestrator URL)以
访问仪表盘。系统会提示你使用已配置的凭据登录。

**默认凭据:**
- 用户名: `admin`
- 密码: `admin`

> **⚠️ 重要提示:** 在生产环境中,请通过设置 `DASHBOARD_USERNAME`、`DASHBOARD_PASSWORD` 和 `DASHBOARD_JWT_SECRET` 环境变量来修改默认凭据。

#### 仪表盘功能

* **概览视图:** 显示 orchestrator 的运行时长、已处理任务总数、成功/失败率、智能体健康概览,以及所有记录任务的汇总 token 消耗和估算成本。
* **智能体网格:** 显示所有已注册的智能体,包括其当前状态(AVAILABLE、BUSY、BROKEN)、能力和最近活动。包含一个手动的"发现智能体"按钮,可按需触发重新发现。
* **任务历史:** 列出最近的任务,包括执行细节、耗时、分配的智能体、状态,以及每个任务的 token 消耗和估算成本。点击任务可查看其执行日志。
* **错误日志:** 显示最近的错误及其上下文,包括堆栈跟踪片段以及相关的任务/智能体信息。
* **日志查看器:** 可筛选的日志查看器,支持按级别筛选(INFO、WARNING、ERROR)、按任务/智能体查询日志,以及分页加载日志("加载更多")。

#### 启动 UI 开发服务器(仅用于开发)

如果你想以热重载方式在开发模式下运行 UI:

```bash
cd orchestrator/ui
npm install
npm run dev
```

开发服务器运行在端口 5173,并配置了到 orchestrator 后端的代理。

#### 构建生产环境 UI

要构建 UI 并将其与 orchestrator 集成:

```bash
cd orchestrator/ui
# Windows
start.bat

# Linux/macOS
./start.sh
```

这将构建 React 应用,并将静态文件复制到 `orchestrator/static/`,供 orchestrator 提供服务。

### Token 预算与成本监控

智能体和 orchestrator 发出的每一次 LLM 调用都会被计量。每次智能体运行结束后,消耗的
token 数(输入/输出/总量、请求数、工具调用数)以及估算的美元成本会:

* 由智能体和 orchestrator **记录**为一行摘要日志,并且
* **展示在仪表盘中**——按任务展示(Tokens/Cost 列),并在概览卡片中作为汇总数据展示。

orchestrator 自身的路由/提取用 LLM 调用同样会被记录。

#### 硬性单任务限制

每次智能体运行都受到**单任务总 token 预算**的限制。当某次运行超出该限制时,该次运行会因
pydantic-ai 的 `UsageLimitExceeded` 而被中止,该任务会被报告为失败。该上限之所以是**基于 token**的,
是因为 pydantic-ai 强制执行的是 token 限制,而不是货币限制——美元数值仅用于监控参考。

| 变量 | 描述 | 默认值 |
|----------|-------------|---------|
| `TOTAL_TOKENS_LIMIT_PER_TASK` | 智能体在单个任务中可消耗的最大 token 总量。 | `1000000` |

#### 成本估算

美元成本是根据 `config.py` 中的静态价格表 `BudgetConfig.MODEL_PRICING` 推算得出的,该表以
pydantic-ai 的模型名称作为键,以每 1,000,000 token 的美元价格(`input`/`output`)表示。请根据你的
提供方公布的定价保持该表更新。不在该表中的模型的成本将报告为 `null`(但其 token 仍会被计数)。

### 部署到 Google Cloud Run

该项目已配置好可部署到 Google Cloud Run。`cloudbuild.yaml` 文件负责编排
Docker 镜像的构建及其作为独立服务的部署。在运行以下任何命令之前,你需要先安装
gcloud CLI。

#### 前置条件:

1. 已存在的 VPC 网络。可以使用以下命令创建:
    ```bash
   gcloud compute networks create agent-network --subnet-mode=custom
   gcloud compute networks subnets create SUBNET_NAME --network=NETWORK_NAME --range=IP_RANGE --region=REGION
    ```
   目标子网还必须启用 Private Google Access,以便在 Google Cloud
   Run 中运行的智能体可以访问
   其他具有"internal"入站规则的智能体(基本上除 orchestrator 外的所有智能体都是如此)。
2. 访问 Secrets Manager 的权限。可以使用以下命令创建:
    ```bash
   gcloud projects add-iam-policy-binding <project_id> --member="serviceAccount:<project_number>-compute@developer.gserviceaccount.com" --role="roles/secretmanager.secretAccessor" 
    ```
3. 用于将请求从 VPC 网络路由到互联网的 Cloud NAT。可以使用以下
   命令创建:
    ```bash
   gcloud compute routers create ROUTER_NAME --network=NETWORK_NAME --region=REGION
   gcloud compute routers nats create NAT_GATEWAY_NAME --router=ROUTER_NAME --region=REGION --nat-all-subnet-ip-ranges 
    ```
4. 需要在 Google Secrets Manager 中添加以下**密钥**及其对应的值:
    * `PHOTON_API_KEY`
    * `FEISHU_APP_ID`
    * `FEISHU_APP_SECRET`
    * `MEEGO_PLUGIN_ID`
    * `MEEGO_PLUGIN_SECRET`
    * `JIRA_API_TOKEN`
    * `JIRA_USERNAME`
    * `JIRA_URL`
    * `ZEPHYR_API_TOKEN`
    * `ZEPHYR_BASE_URL`
    * `JIRA_MCP_SERVER_URL`
    * `ORCHESTRATOR_API_KEY`
5. 用于一般操作的 Cloud Storage bucket(需创建好所有必要的文件夹,参见"替换变量")。
6. 用于存储并公开提供测试执行报告的 Cloud Storage bucket(该 bucket 需要具有公开
   访问权限)

#### 部署:

在满足所有前置条件后,你可以执行以下命令:

```bash
gcloud builds submit --config 'path/to/your/cloudbuild.yaml' --substitutions "^;^_BUCKET_NAME=YOUR_GCS_BUCKET_NAME;_ALLURE_REPORTS_BUCKET=YOUR_ALLURE_REPORTS_BUCKET_NAME;_REQUIREMENTS_REVIEW_AGENT_BASE_URL=YOUR_REQUIREMENTS_REVIEW_AGENT_URL;_TEST_CASE_GENERATION_AGENT_BASE_URL=YOUR_TEST_CASE_GENERATION_AGENT_URL;_TEST_CASE_CLASSIFICATION_AGENT_BASE_URL=YOUR_TEST_CASE_CLASSIFICATION_AGENT_URL;_TEST_CASE_REVIEW_AGENT_BASE_URL=YOUR_TEST_CASE_REVIEW_AGENT_URL;_INCIDENT_CREATION_AGENT_BASE_URL=YOUR_INCIDENT_CREATION_AGENT_URL;_REMOTE_EXECUTION_AGENT_HOSTS=YOUR_COMMA_SEPARATED_AGENT_HOSTS;_PROMPT_GUARD_SERVICE_URL=YOUR_PROMPT_GUARD_SERVICE_URL;_DEPLOY_ALL_SERVICES=true" .
```

```powershell
gcloud builds submit --config 'path/to/your/cloudbuild.yaml' --substitutions "`^;`^_BUCKET_NAME=YOUR_GCS_BUCKET_NAME;_ALLURE_REPORTS_BUCKET=YOUR_ALLURE_REPORTS_BUCKET_NAME;_REQUIREMENTS_REVIEW_AGENT_BASE_URL=YOUR_REQUIREMENTS_REVIEW_AGENT_URL;_TEST_CASE_GENERATION_AGENT_BASE_URL=YOUR_TEST_CASE_GENERATION_AGENT_URL;_TEST_CASE_CLASSIFICATION_AGENT_BASE_URL=YOUR_TEST_CASE_CLASSIFICATION_AGENT_URL;_TEST_CASE_REVIEW_AGENT_BASE_URL=YOUR_TEST_CASE_REVIEW_AGENT_URL;_INCIDENT_CREATION_AGENT_BASE_URL=YOUR_INCIDENT_CREATION_AGENT_URL;_REMOTE_EXECUTION_AGENT_HOSTS=YOUR_COMMA_SEPARATED_AGENT_HOSTS;_PROMPT_GUARD_SERVICE_URL=YOUR_PROMPT_GUARD_SERVICE_URL;_DEPLOY_ALL_SERVICES=true" .
```

**替换变量:**
* `_BUCKET_NAME`: 用于存储 Jira MCP server 下载的附件的 Google Cloud Storage bucket
  名称。
* `_JIRA_ATTACHMENTS_FOLDER`: Jira MCP server 保存附件的文件夹名称,必须
  与 'JIRA_ATTACHMENTS_CLOUD_STORAGE_FOLDER' 环境变量一致
* `_ALLURE_REPORTS_BUCKET`: 用于存储测试执行 HTML 报告的 GCS bucket。
* `_REQUIREMENTS_REVIEW_AGENT_BASE_URL`: 已部署的需求评审智能体的 URL。
* `_TEST_CASE_GENERATION_AGENT_BASE_URL`: 已部署的测试用例生成智能体的 URL。
* `_TEST_CASE_CLASSIFICATION_AGENT_BASE_URL`: 已部署的测试用例分类智能体的 URL。
* `_TEST_CASE_REVIEW_AGENT_BASE_URL`: 已部署的测试用例评审智能体的 URL。
* `_INCIDENT_CREATION_AGENT_BASE_URL`: 已部署的缺陷创建智能体的 URL。
* `_REMOTE_EXECUTION_AGENT_HOSTS`: orchestrator 将与之交互的所有已部署智能体的 URL,以逗号
  分隔。
* `_PROMPT_GUARD_SERVICE_URL`: 已部署的 Prompt Guard 服务的 URL。
* `_DEPLOY_ALL_SERVICES`: 设置为 `true` 以部署所有服务。也提供了单独的服务开关(例如 `_DEPLOY_JIRA_MCP`)以支持精细化部署。

**重要提示**: 在首次将该框架部署到 Google Cloud Run 之前,通常很难预先知道
每个智能体和 orchestrator 会被分配到哪个 URL。因此你很可能需要先运行一次部署命令,
然后确定每个服务被分配到的 URL,再更新命令中的替换变量值,重新运行一次。

### 隔离环境的烟雾测试(Hermetic smoke tests)

烟雾测试套件是一个自包含的集成测试,独立于任何 Cloud Run 部署。它在
`docker-compose.smoke.yml` 下运行真实的 orchestrator 和各个 QA 智能体(需求评审、测试用例生成、
分类、评审以及缺陷创建),由通过 Photon API relay 调用的真实模型驱动,仅将外部边界替换为
`tests/smoke/mocks/` 下的 mock(Jira MCP、Jira REST、飞书 MCP、飞书项目/Meego、Zephyr 和 Qdrant)。一个 mock 测试执行智能体代替了托管在 VM 上的真实执行器。它通过 orchestrator 的公开 webhook 驱动整个系统,并对到达每个被 mock 的边界的内容进行断言:

* **需求评审**(`POST /requirement-ready-for-review`)→ 一条非空的评审评论到达飞书项目中被评审的工作项(Meego mock),
  且智能体首先通过飞书 MCP 获取了需求文档。
* **测试用例生成**(`POST /story-ready-for-test-case-generation`)→ 真实的测试用例(名称 + 步骤)被创建到飞书项目(Meego mock)中,并回链到原始 story ID。
* **测试用例分类**(同一个 webhook)→ 标签被应用到飞书项目中的测试用例工作项上。
* **测试用例评审**(同一个 webhook)→ 评审评论和状态流转到达飞书项目中的测试用例工作项。
* **测试执行 / 缺陷创建**(`POST /execute-tests`)→ 一个失败的自动化测试会驱动一个真实的 Bug issue 进入
  预置的 Jira 项目,失败的执行会在一个新的测试周期中报告给 Zephyr,该 bug 会关联到
  该次执行,并且重复检测搜索会查询向量数据库。
* **RAG DB 更新**(`POST /update-rag-db`)→ 同步操作会将预置的 Jira story 推送到被 mock 的向量数据库中
  (包括集合创建和数据点 upsert)。
* **负向路径**→ 另外三个鉴权型 webhook 均会拒绝无效的 API key(401);`/requirement-ready-for-review`
  按设计不鉴权,遇到格式不对的 payload 或还没填 PRD 链接的情况会直接静默丢弃(204);
  缺失 `story_id`/`feishu_doc` 时返回 400,缺失 `project_key` 时返回 422,且仪表盘 API 会拒绝缺失
  token 的请求(401)——所有这些情况都不会分发给任何智能体。

四个 webhook 会一次性、并发地触发(因为这些流程彼此独立),因此该套件的墙钟时间
取决于最长的单个流程,而不是所有流程耗时之和。

它只在 GitHub Actions(`.github/workflows/ci.yml` 中的 `smoke` job)中,在推送到 `main` 分支以及手动
`workflow_dispatch` 时运行——从不在 pull request 中运行——因为每次运行都会通过 Photon API
relay 产生真实的、计费的 LLM 调用。该 job 需要一个 `PHOTON_API_KEY` 仓库密钥。要在本地运行它:

```bash
docker build -t agentic-qa-base:latest -f Dockerfile.base .
PHOTON_API_KEY=<your-key> docker compose -f docker-compose.smoke.yml up -d --build --wait
uv run pytest tests/smoke -m smoke -v
docker compose -f docker-compose.smoke.yml down -v
```

## 触发 Orchestrator 工作流

### 通过飞书项目 Webhook 触发工作流

Orchestrator 监听来自飞书项目自动化规则的 webhook,以启动自动化工作流。

* **Story 已可进行需求评审:**
  在飞书项目后台为 story 工作流配置一条自动化规则:进入需求设计状态时,执行"发送 HTTP 请求"动作,
  目标地址为 `/requirement-ready-for-review`。飞书会发送其原生的 `WorkFlowNodeStatusEvent` 载荷——
  orchestrator 从中提取工作项 ID、项目 key,以及 PRD 文档链接(工作项表单里第一个 `link` 类型字段)。
  该请求**不鉴权**(飞书的自动化动作无法附带自定义 header),转而由按工作项去重窗口 + 全局速率上限
  来控制风险面——见[架构即代码(CALM)](#架构即代码calm)。PRD 链接尚未填写,或未通过去重/限流检查的
  请求,会被静默丢弃(204),而不是当作错误处理。

* **Story 已准备好进行测试用例生成:**
  向 `/story-ready-for-test-case-generation` 发送 POST 请求,请求体包含飞书项目的
  `story_id`、空间的 `project_key` 以及 PRD 文档的 `feishu_doc` URL 或 token。
  这将触发测试用例生成、分类和评审工作流,并将生成的测试用例作为工作项回写到飞书项目中。

  示例请求体:
  ```json
  {
      "story_id": "7082225030",
      "project_key": "union-platform",
      "feishu_doc": "https://photonpay.feishu.cn/wiki/SKNKw8iili9Q2ike5f5cdqgPnhb"
  }
  ```

### 执行自动化测试

你可以为特定项目触发自动化测试的执行。

* **执行测试:**
  向 `/execute-tests` 发送 POST 请求,请求体为 JSON,包含 Jira 项目的 `project_key`。这
  将执行该项目中所有标记为"automated"的测试用例。对于任何失败的测试,orchestrator 将
  自动使用缺陷创建智能体触发缺陷创建。

  示例请求体:
  ```json
  {
      "project_key": "SCRUM"
  }
  ```
  结果会被报告回 Zephyr,并生成一份 Allure 报告。

### 更新 RAG 向量数据库

为保持向量数据库与 Jira issue 的同步,以支持重复检测:

* **更新 RAG DB:**
  向 `/update-rag-db` 发送 POST 请求,请求体为 JSON,包含 Jira 项目的 `project_key`。
  orchestrator 随后会将该项目的 issue 从 Jira(直接通过 Jira REST API 读取)同步到 Qdrant 向量
  数据库中,从而支持用于重复检测的语义搜索。该同步以编程方式运行——不涉及任何 LLM 智能体。

  示例请求体:
  ```json
  {
      "project_key": "SCRUM"
  }
  ```

### 仪表盘 API 端点

仪表盘提供 REST API 端点,以便以编程方式访问监控数据。所有仪表盘端点都需要 JWT 认证。

**认证:**

* `POST /api/auth/login` - 认证并获取 JWT 令牌。
  ```json
  {"username": "admin", "password": "admin"}
  ```
* `POST /api/auth/logout` - 登出(客户端移除令牌)。
* `GET /api/auth/verify` - 校验当前令牌是否有效。

**仪表盘数据:**

* `GET /api/dashboard/summary` - 获取高层统计数据(运行时长、任务数、智能体健康状况)。
* `GET /api/dashboard/agents` - 获取所有已注册智能体的详细状态。
* `GET /api/dashboard/tasks?limit=50` - 获取最近的任务及其执行细节。
* `GET /api/dashboard/errors?limit=20` - 获取最近的错误及其上下文。
* `GET /api/dashboard/logs?limit=100&offset=0&level=ERROR&task_id=xxx&agent_id=yyy` - 获取经过筛选的应用日志(支持通过 `offset` 分页)。
* `POST /api/dashboard/discovery` - 手动触发智能体发现。

## A2A 流式传输契约

QuAIA™ 使用 A2A artifact 机制,在任务运行期间将实时更新从智能体推送到
orchestrator 仪表盘。

### `report_activity` 工具

每一个通过 `AgentBase` 创建的智能体都会自动获得一个 `report_activity` 工具,以及一段
附加到其系统提示词末尾的一行说明片段。编写智能体提示词模板的开发者**无需**
手动包含这些内容——它们由 `AgentBase.__init__` 注入。

LLM 会调用 `report_activity(description)`,传入一句简短的话(≤ 120 字符)来描述
当前的推理阶段或即将调用的工具。每次调用都会被转发到
仪表盘,作为一个 `agent_activity` artifact。

### 流式 Artifact

以下两种 artifact 类型会被 orchestrator 的分块处理循环识别。
`agent_activity` 由每个 `AgentBase` 智能体自动发出。`agent_logs_stream`
是**可选的**——缺少它并不算错误;仪表盘会优雅降级。

所有的负载模式(payload schema)都带有一个 `version` 字段,以便在不破坏
外部消费者的情况下演进传输格式。

#### `agent_activity`

在每次 `report_activity` 调用时发出。仅显示最新的文本——每当有新事件到来,
仪表盘就会覆盖之前的活动内容(不保留历史记录)。

```json
{
  "version": 1,
  "type": "agent_activity",
  "task_id": "<internal-task-id>",
  "agent_id": "<agent-id>",
  "text": "Fetching Jira issue PROJ-123"
}
```

#### `agent_logs_stream`(可选)

由 `DefaultAgentExecutor` 每 2 秒刷新一次的日志批次。不使用
该执行器的外部智能体不会发出此 artifact;仪表盘会回退为轮询日志。

```json
{
  "version": 1,
  "type": "log_batch",
  "task_id": "<internal-task-id>",
  "lines": ["2025-05-17 12:00:01 INFO  fetching issue", "..."]
}
```

#### `agent_usage`(可选)

一次运行完成后,由 `DefaultAgentExecutor` 发出的单个 `application/json` artifact(名称为
`agent_usage`),携带该次运行的 token 使用量和估算成本。orchestrator 会将其记录到任务上并为
仪表盘进行汇总。缺少它并不算错误。

```json
{
  "model_name": "claude-sonnet-5",
  "input_tokens": 1200,
  "output_tokens": 340,
  "total_tokens": 1540,
  "cache_read_tokens": 0,
  "requests": 2,
  "tool_calls": 3,
  "cost_usd": 0.0012
}
```

### 仪表盘 SSE 流

仪表盘通过两个 Server-Sent Event(SSE)端点接收流式更新。

#### 流令牌(Stream-Token)认证

SSE 端点使用一个独立的短生命周期令牌,而不是长生命周期的 JWT,这样即使
一个 URL 被截获(浏览器历史记录、代理日志),一旦该流过期,也无法被重放。

**流程:**

1. UI 使用标准的
   `Authorization: Bearer <jwt>` 头,向 `POST /api/dashboard/stream-token` 发起 POST 请求。
2. 服务器生成一个**5 分钟 TTL** 的不透明令牌,并返回
   `{"stream_token": "...", "expires_at": "..."}`。
3. UI 使用查询参数 `?stream_token=<token>` 打开 `EventSource`。
4. 每 15 秒,服务器会发送一个 `heartbeat` 帧,并重新校验令牌的有效期。
   一旦过期,服务器会发出一次性的 `event: auth_error` 帧并关闭连接;
   UI 的 401 处理逻辑会跳转到登录页。

#### SSE 端点

| 端点 | 描述 |
|---|---|
| `POST /api/dashboard/stream-token` | 生成一个 5 分钟的流令牌(需要 Bearer JWT)。 |
| `GET /api/dashboard/stream?stream_token=<token>` | 全局流:初始的 `snapshot` 帧 + 实时的 `agent_activity`、`task_done` 和 `gap` 事件。 |
| `GET /api/dashboard/agents/{agent_id}/stream?stream_token=<token>` | 单个智能体的流:日志弹窗使用的实时 `log_batch` 事件。 |

全局流的第一帧会带有 `event: snapshot`,携带当前的智能体
注册表以及所有正在运行的任务及其最新的活动文本。

---

## 运行测试

该项目包含一个完整的测试套件。运行测试:

```bash
# 运行所有测试
uv run pytest

# 以详细输出方式运行测试
uv run pytest -v

# 运行特定模块的测试
uv run pytest tests/agents/
uv run pytest tests/orchestrator/
uv run pytest tests/common/
```

`tests/smoke/` 下的套件被标记为 `smoke`,它驱动的是上文
[隔离环境的烟雾测试](#隔离环境的烟雾测试hermetic-smoke-tests)中所述的隔离 docker-compose 拓扑,而不是孤立地测试本地代码。因为它需要该
技术栈处于运行状态,所以默认情况下(通过 `pytest.ini` 中的 `addopts`)会被排除在裸的 `uv run pytest` 之外,
使本地运行保持无害。一旦该技术栈启动完成,可通过 `uv run pytest -m smoke` 显式运行它。它只在 CI 中,
在推送到 `main` 分支以及手动 `workflow_dispatch` 时运行(参见上文的*隔离环境的烟雾测试*)。

## 贡献

我们欢迎为 QuAIA™ 做出贡献!请参阅 [CONTRIBUTING.md](CONTRIBUTING.md) 以了解
贡献指南。

## 许可证

该项目基于 GNU Affero General Public License v3.0(AGPL-3.0)许可 - 详情请参阅 [LICENSE](LICENSE) 文件。
