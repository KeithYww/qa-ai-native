# Photon Agentic Tester — 深度学习指南

这份文档是项目的「知识地图」，目标是帮你在参与开发的同时，系统性地理解每个设计决策背后的原因。建议边看代码边读，遇到不懂的地方直接跳进对应文件。

---

## 目录

1. [项目整体架构](#1-项目整体架构)
2. [核心协议：A2A](#2-核心协议a2a)
3. [Agent 怎么写的：AgentBase 解析](#3-agent-怎么写的agentbase-解析)
4. [Orchestrator：任务调度的核心逻辑](#4-orchestrator任务调度的核心逻辑)
5. [LLM 接入层：CustomLlmWrapper](#5-llm-接入层customllmwrapper)
6. [实时流：SSE + StreamingHub](#6-实时流sse--streaminghub)
7. [测试体系：三层防线](#7-测试体系三层防线)
8. [架构治理：CALM](#8-架构治理calm)
9. [依赖管理：uv + pyproject.toml](#9-依赖管理uv--pyprojecttoml)
10. [值得深挖的设计亮点](#10-值得深挖的设计亮点)

---

## 1. 项目整体架构

```
飞书项目 (Webhook) ──► Orchestrator (8000)
                            │
              ┌─────────────┼─────────────────┐
              ▼             ▼                 ▼
       Generation     Classification       Review
        Agent          Agent              Agent
        (8002)         (8003)             (8004)
              │
        Feishu MCP
        Server (9010)
              │
         飞书文档 API
```

**关键点**
- Orchestrator 不执行任何 AI 逻辑，它只做「任务路由 + 状态管理」
- 每个 Agent 是独立的 FastAPI 进程，通过 A2A 协议通信
- Feishu MCP Server 是一个 SSE 服务，把飞书文档 API 包装成 AI 可调用的工具
- 所有外部系统（飞书项目、Jira、Zephyr）通过 client 层隔离，Agent 不直接调 HTTP

**文件入口**
- `orchestrator/main.py` — 主编排逻辑（1700+ 行，是理解整个系统的核心）
- `common/agent_base.py` — 所有 Agent 的基类
- `config.py` — 所有配置的唯一来源

---

## 2. 核心协议：A2A

### 什么是 A2A？

A2A（Agent-to-Agent）是 Google 提出的一个 Agent 间通信协议，基于 JSON-RPC over HTTP。项目使用 `a2a-sdk==1.0.3`。

核心模型：

```
Orchestrator                          Agent
    │                                   │
    │── POST / (SendMessageRequest) ──►  │
    │                                   │  执行任务（可能很长）
    │◄── streaming events (SSE) ────────│
    │    ├─ TaskArtifactUpdateEvent      │  日志 chunk
    │    ├─ TaskStatusUpdateEvent        │  working / completed / failed
    │    └─ ...                          │
```

### Agent Card（服务发现）

每个 Agent 在 `/.well-known/agent.json` 暴露自己的「名片」：名称、能力、接口 URL。Orchestrator 每 5 分钟扫描 8001-8007 端口，读取所有 Agent Card，注册到 `AgentRegistry`。

**学习点**：这是「服务发现」的最简实现。没有 etcd、没有 Consul，只是一个 dict + asyncio.Lock。

```python
# orchestrator/models.py — AgentRegistry
class AgentRegistry:
    def __init__(self):
        self._cards: dict[str, AgentCard] = {}
        self._statuses: dict[str, AgentStatus] = {}
        self._lock = asyncio.Lock()
```

### Artifact（任务产物）

任务完成后，Agent 会返回多个 Artifact：
- `agent_execution_result`：JSON 格式的主结果（GeneratedTestCases、ClassifiedTestCases 等）
- `logs`：分批流式上传的执行日志（`media_type="text/plain"`）
- `agent_usage`：token 消耗和费用估算

**学习点**：`ArtifactName` 常量定义在 `common/a2a_contract.py`，这是 Orchestrator 和 Agent 之间唯一的「约定文档」。

---

## 3. Agent 怎么写的：AgentBase 解析

### 继承结构

```
AgentBase (common/agent_base.py)
    │
    ├─ TestCaseGenerationAgent
    ├─ TestCaseClassificationAgent
    ├─ TestCaseReviewAgent
    ├─ RequirementsReviewAgent
    └─ IncidentCreationAgent
```

每个 Agent 只需要重写 3 个方法：

```python
class MyAgent(AgentBase):
    def get_thinking_level(self) -> ThinkingLevel:
        return "medium"          # Claude 的思考深度

    def get_max_requests_per_task(self) -> int:
        return 30                # 最多调用 LLM/工具多少次

    def get_max_tokens(self) -> int | None:
        return 16000             # 单次 LLM 响应最大 token
```

### pydantic-ai Agent

项目用 `pydantic-ai` 作为 Agent 框架。核心概念：

```python
# 每个 AgentBase 内部创建一个 pydantic_ai.Agent
agent = Agent(
    model=CustomLlmWrapper(...),     # LLM 适配层
    output_type=ClassifiedTestCases, # 强类型输出，LLM 必须返回此结构
    instructions=system_prompt,      # 系统提示词
    tools=[self.add_labels_to_test_case, self.report_activity],
    deps_type=TestCaseKeys,          # 依赖注入类型
)
```

**学习点**：`output_type` 是 pydantic-ai 最核心的特性。它不是让 LLM 返回任意文本，而是强制 LLM 调用一个隐式的结构化输出工具。如果 LLM 不满足 schema，框架会自动 retry（最多 `output_retries=3` 次）。

### 工具函数（Tool）

Agent 可以调用「工具」，工具是普通 Python 函数，docstring 就是工具的说明：

```python
@staticmethod
def add_labels_to_test_case(test_case_key: str, labels: list[str]) -> str:
    """
    Adds labels to a test case.        ← LLM 看到的工具描述
    Args: ...
    Returns: ...
    """
    client = get_test_management_client()
    client.add_labels_to_test_case(test_case_key, labels)
    return f"Successfully added labels..."
```

### report_activity：实时状态上报

AgentBase 自动注入 `report_activity` 工具，Agent 每做一步都可以调它：

```python
async def report_activity(self, description: str) -> None:
    """Report your current activity to the dashboard."""
    self._activity_queue.put_nowait(description)
```

Executor 在后台 `flush_activity_loop` 把这些消息推给 Orchestrator，最终显示在 Dashboard 的实时状态栏。

---

## 4. Orchestrator：任务调度的核心逻辑

### Agent 选择算法

当有任务需要执行时，Orchestrator 会：

1. **等待可用 Agent**：轮询 `AgentRegistry`，直到有 AVAILABLE 的 Agent
2. **LLM 选型**：把所有可用 Agent 的名称和描述交给 LLM，让 LLM 决定选哪个
3. **原子预订**：在 `agent_selection_lock` 保护下，把 Agent 状态改为 BUSY

```python
# 关键：选择和预订必须原子完成，否则两个并发任务会选到同一个 Agent
async with agent_selection_lock:
    agent_id = await _select_agent_id(task_description)
    await agent_registry.update_status(agent_id, AgentStatus.BUSY)
```

**学习点**：这是一个典型的「check-then-act」竞争条件，必须用锁保护。

### 任务超时与恢复

```python
# config.py
TASK_EXECUTION_TIMEOUT = float(os.environ.get("TASK_EXECUTION_TIMEOUT", "1200"))  # 20 分钟
```

如果任务超时，Agent 被标记为 `BROKEN/TASK_STUCK`，并加入 `cancellation_queue`。有一个后台任务 `_monitor_broken_agents` 会尝试向该 Agent 发取消请求，然后重置状态。

### 三段式测试流水线

```
/story-ready-for-test-case-generation
    ↓
1. _request_test_cases_generation(feishu_doc)
        → Generation Agent 读飞书文档，生成测试用例
        → 写入飞书项目 (_write_test_cases_to_feishu_project)
    ↓
2. _request_test_cases_classification(test_cases)
        → Classification Agent 给每个测试用例打标签 (ui/api/automated/manual...)
    ↓
3. _request_test_cases_review(test_cases, feishu_doc)
        → Review Agent 审查测试用例质量，写评审意见
```

三个步骤串行执行，每步都依赖上一步的输出。这是有意为之——Classification 依赖真实的 test case key（写入飞书项目后才有）。

---

## 5. LLM 接入层：CustomLlmWrapper

### 为什么要包装？

项目用的不是官方 Anthropic API，而是公司内部的「Photon API」中继（Anthropic 兼容协议）。这个中继：
- 支持多个模型（claude、gpt、deepseek、qwen 等）用同一个 endpoint
- 模型名字是内部别名（如 `claude-sonnet-5`、`deepseek-v4-flash`）
- 某些模型的 thinking 参数格式不同（adaptive vs budget-based）

### FallbackModel 双保险

```python
# common/llm_provider.py
def get_model(primary_model_name: str, fallback_model_name: str) -> Model:
    primary = AnthropicModel(primary_model_name, provider=provider, ...)
    fallback = AnthropicModel(fallback_model_name, provider=provider, ...)
    return FallbackModel(primary, fallback)
```

主模型出错时（429、500、502、503、504），pydantic-ai 的 `FallbackModel` 自动切到备用模型重试。

### Thinking Level

```python
# 不同 Agent 用不同的思考深度，平衡速度和质量
class TestCaseClassificationAgentConfig:
    THINKING_LEVEL = "minimal"  # 分类任务简单，不需要深度思考

class TestCaseGenerationAgentConfig:
    THINKING_LEVEL = "medium"   # 生成任务需要理解需求

class TestCaseReviewAgentConfig:
    THINKING_LEVEL = "medium"   # 审查需要一定推理能力
```

---

## 6. 实时流：SSE + StreamingHub

### 数据流路径

```
Agent (执行中)
    │ report_activity → activity_queue
    │ log lines → AgentLogCaptureHandler
    ▼
DefaultAgentExecutor
    │ flush_activity_loop  → TaskStatusUpdateEvent (WORKING + message)
    │ flush_logs_loop      → TaskArtifactUpdateEvent (logs artifact, streaming)
    ▼
Orchestrator (_send_task_to_agent)
    │ 消费 A2A SSE events
    │ streaming_hub.publish(AgentActivityEvent / LogBatchEvent)
    ▼
Dashboard (/api/dashboard/stream SSE endpoint)
    │ Server-Sent Events
    ▼
Browser (React)
```

### ContextVar：避免日志串流

多个 Agent 同时运行时，日志不能混在一起。用 `contextvars.ContextVar` 把日志 handler 绑定到当前协程上下文：

```python
# common/streaming.py
current_log_handler: ContextVar["AgentLogCaptureHandler | None"] = ContextVar(...)

# agent_executor.py - 每个 execute() 调用有自己的 handler
handler_token = set_current_log_handler(log_handler)
# ... 执行完后 ...
reset_current_log_handler(handler_token)
```

**学习点**：这是 Python asyncio 中隔离「请求级状态」的标准做法，类似 Go 的 context 或 Java 的 ThreadLocal。

---

## 7. 测试体系：三层防线

### 第一层：单元测试（`tests/`，不含 smoke）

- 不启动真实 Agent，不调用真实 LLM
- 用 `openai:gpt-4o-mini` 模型字符串（测试用，pydantic-ai 内建）
- 覆盖每个 Agent 的配置、prompt 加载、工具注册

```bash
uv run pytest tests/ -m "not smoke" -v
```

### 第二层：集成测试（`tests/common/services/`）

- 测试各 client 层（ZephyrClient、MeegoClient、XrayClient）
- 用 mock HTTP 服务或 respx 拦截请求

### 第三层：端到端烟雾测试（`tests/smoke/`）

**这是项目最独特的测试设计**。它在真实 docker-compose 环境中：
- 启动真实 Orchestrator 和真实 Agent（用真实 Gemini 模型）
- 只 mock 外部边界（飞书 API、Jira、Zephyr、Qdrant）
- 断言什么内容到达了 mock

```
docker-compose.smoke.yml
    ├─ orchestrator
    ├─ generation_agent
    ├─ classification_agent
    ├─ review_agent
    ├─ feishu_mcp_mock     ← FastAPI，返回假文档内容
    ├─ meego_mock          ← FastAPI，记录写入请求
    ├─ zephyr_mock
    └─ qdrant_mock
```

**为什么这样设计**：单元测试 mock 太多，容易掩盖真实问题。这套烟雾测试能发现「组件各自测试通过，但组合后出错」的问题。

CI 限制：烟雾测试只在 PR 和手动触发时跑（要花 Gemini API 费用），不在每次 push 时跑。

---

## 8. 架构治理：CALM

### 什么是 CALM？

[FINOS CALM](https://calm.finos.org/) 是「Architecture as Code」标准——把系统架构写成 JSON Schema，并用 CLI 工具验证「实际架构」是否符合「治理模板」。

### 文件结构

```
calm/
├─ architecture/quaia.arch.json    ← 实际架构：节点（服务）+ 关系（调用边）+ 控制（安全要求）
├─ patterns/quaia.pattern.json     ← 治理模板：哪些节点/关系/控制是强制要求的
└─ url-mapping.json                ← 模式 URL → 本地文件路径映射
```

### 验证命令

```bash
cd calm
npx -y @finos/calm-cli@1.46.0 validate \
    -p patterns/quaia.pattern.json \
    -a architecture/quaia.arch.json \
    -u url-mapping.json \
    --strict -f pretty
```

**规则**：每次新增服务、新增调用关系、新增安全控制，必须同步更新 `quaia.arch.json`。否则 CI 的 `Architecture (CALM)` job 会挂。

**学习价值**：传统项目架构图是 Confluence 上的 PNG，和代码完全脱节。CALM 让架构图成为 CI 的一部分，做到「代码即文档，文档即约束」。

---

## 9. 依赖管理：uv + pyproject.toml

项目用 [uv](https://docs.astral.sh/uv/) 替代 pip/poetry，主要优势：极快的依赖解析和安装。

### 依赖分层

```toml
# pyproject.toml

[project.dependencies]          # 运行时必须
    pydantic-ai-slim[google,anthropic,mcp]==1.89.0
    a2a-sdk==1.0.3
    ...

[project.optional-dependencies] # 按服务选装
embedding-service = [...]        # 只在 embedding 服务镜像里装
prompt-guard-service = [...]     # 只在 prompt guard 服务镜像里装

[dependency-groups]
dev = [                          # 只在开发/CI 环境装
    "pytest==9.0.3",
    "pydantic-ai-slim[openai]==1.89.0",  # 测试用 openai provider
]
```

### 常用命令

```bash
uv sync                          # 安装所有依赖（包含 dev）
uv sync --only-group dev         # 只安装 dev 工具（CI lint job）
uv run pytest ...                # 在虚拟环境里跑命令，不需要激活 venv
uv add httpx                     # 添加依赖（自动更新 uv.lock）
```

---

## 10. 值得深挖的设计亮点

### 10.1 Fail-Closed 安全设计

整个系统的默认行为是「配置缺失时拒绝服务」而不是「降级放行」：

```python
# Dashboard 登录：三个值任意一个为空，所有登录都被拒
class DashboardAuthConfig:
    USERNAME = os.environ.get("DASHBOARD_USERNAME", "")   # 空字符串 = 禁用
    PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
    JWT_SECRET = os.environ.get("DASHBOARD_JWT_SECRET", "")
```

```python
# Orchestrator API：没有配置 key，所有 webhook 请求都被拒
API_KEY = os.environ.get("ORCHESTRATOR_API_KEY")   # None = fail closed
```

### 10.2 ExecuteLock vs SelectionLock

```python
execution_lock = asyncio.Lock()       # 串行执行整个工作流（防止并发运行两个流程）
agent_selection_lock = asyncio.Lock() # 原子选择 + 预订 Agent（防止两个任务抢同一个 Agent）
```

两把锁粒度不同，目的不同。搞清楚为什么需要两把而不是一把，是理解并发设计的好练习。

### 10.3 Prompt Injection 防护

```python
# CustomLlmWrapper.request() 中，每次 LLM 调用前检查
if config.PROMPT_INJECTION_CHECK_ENABLED:
    self._validate_for_prompt_injection(messages)
```

检查的是最后一条用户消息或工具返回值——因为这两个地方最容易被注入（外部 API 返回的内容、用户提交的文档）。

### 10.4 ContextVar 日志隔离

见第 6 节。这是 asyncio 中「请求级上下文」传递的标准解法，值得单独写代码实验。

### 10.5 Token 预算控制

```python
# agent_executor.py
usage_limits = UsageLimits(
    tool_calls_limit=compute_activity_budget(self.get_max_requests_per_task()),
    total_tokens_limit=self.get_total_tokens_limit(),
)
```

`compute_activity_budget` 把 `max_requests_per_task` 乘以 2，因为 `report_activity` 本身也是工具调用，占用配额。这个细节很容易漏掉，导致 Agent 比预期提前停止。

### 10.6 MCP：把任意 API 变成 AI 工具

```python
# scripts/feishu_mcp_server.py
mcp = FastMCP("feishu-mcp")

@mcp.tool()
async def feishu_get_doc_content(doc_token_or_url: str) -> str:
    """..."""   # docstring = 工具描述，LLM 看到它来决定何时调用
    ...

app = mcp.sse_app()   # 暴露为 SSE 服务
```

Agent 端：

```python
# agents/test_case_generation/main.py
super().__init__(
    mcp_servers=[MCPServerSSE(url=config.FEISHU_MCP_SERVER_URL)],
    ...
)
```

pydantic-ai 自动把 MCP 服务器的工具列表注入给 Agent，Agent 可以像调用本地工具一样调用远程工具。

---

## 延伸阅读

| 主题 | 推荐资源 |
|------|---------|
| pydantic-ai | https://ai.pydantic.dev/ |
| A2A Protocol | https://google.github.io/A2A/ |
| FINOS CALM | https://calm.finos.org/ |
| MCP (Model Context Protocol) | https://modelcontextprotocol.io/ |
| uv | https://docs.astral.sh/uv/ |
| asyncio ContextVar | Python 官方文档 contextvars 模块 |
| SSE (Server-Sent Events) | MDN Web Docs |
