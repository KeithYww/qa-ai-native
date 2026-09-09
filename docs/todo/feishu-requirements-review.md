# 需求评审：Jira → 飞书 迁移设计 + TODO

## 背景

`agents/requirements_review` 现在 100% 依赖 Jira MCP，飞书那边没有任何等价实现（详见本次会话对 Jira 依赖的全仓库梳理）。目标：把这个能力迁移到飞书体系,然后才能着手删除 Jira 相关代码。

这是"先补能力、再退役 Jira"三阶段计划的第一阶段（后两阶段：缺陷创建、RAG 同步,各自单独立一个 TODO）。

## 设计定案：飞书项目自动化"发送 HTTP 请求"动作，接原生事件结构

**已确认**：飞书项目自动化原生支持"触发条件（比如需求流转到某状态）→ 动作：发送 HTTP 请求 → 填目标 URL"，不需要对接专门配置的"AI 节点"类型（"需求设计"是普通工作流状态，不是 AI 节点）。

**但载荷格式不是我们能自定义的扁平 JSON**——调研内部姊妹项目（`herenjian/testplatform`）里同一个"发送 HTTP 请求"动作的真实生产实现（`prd_review/views.py::feishu_project_webhook`）发现：

- **完全没有自定义鉴权 header**——那个端点只有 `@csrf_exempt @require_POST`，什么校验都没有。大概率是这个动作**不支持自定义 header**，不是团队偷懒（安全意识正常的团队不会平白无故不做鉴权）
- **Body 是飞书自己的原生事件结构**（`WorkFlowNodeStatusEvent`），不是自定义字段名：

  ```python
  payload = body.get("payload") or body
  work_item_id = payload.get("id")
  project_key = payload.get("project_simple_name")
  # PRD 链接不是顶层字段，是从工作流节点表单里扫出来的
  for node in payload.get("nodes") or []:
      for field in node.get("node_form") or []:
          if field.get("field_type_key") == "link":
              feishu_url = field["field_value"]
  ```

**结论**：新端点不能照抄现有 `/story-ready-for-test-case-generation` 假设的那套自定义 `{story_id, project_key, feishu_doc}` 扁平 JSON——要照 `feishu_project_webhook` 的方式解析飞书原生结构。（现有那个端点是不是真的被飞书原生自动化触发过、还是走的别的路子，用户确认"不确定，需要先查"——这是另一个需要验证的点，不阻塞这次的新端点设计，但值得记一下，可能是个隐藏的坑。）

### 安全影响：这个端点大概率没法做 Header 鉴权

既然飞书这个动作不支持自定义 header，新端点的鉴权就不能是"发一个 X-API-Key 校验"这种常规做法。

**已决定：不鉴权，靠限流 + 严格校验 payload 结构控制风险面**。具体要求：

- **按 `work_item_id` 限流**：同一个 `work_item_id` 短时间内（比如 5 分钟）只处理一次，重复请求直接丢弃（返回 2xx 但不入队），不重复触发评审——这同时也顺手处理了"飞书自动化失败重试"或者误触发两次的情况
- **全局限流**：加一个粗粒度的总请求速率上限（比如每分钟 N 次），防止 URL 一旦泄露被批量刷,把 LLM 预算刷爆
- **严格校验 payload 结构**：解析不出 `work_item_id` 或 PRD 链接字段的请求直接丢弃，不当成合法请求处理——随手来的垃圾请求大概率对不上飞书原生事件结构，这本身就是一层过滤
- 限流状态可以先用进程内内存结构（字典 + 时间戳），不需要引入 Redis 之类的新基础设施,跟这个项目一贯的 MVP 优先原则一致

## 字段名已确认：直接照抄姊妹项目

**已确认**：跟 `herenjian/testplatform` 共用同一个飞书项目空间（`union-platform`），自动化配置也一样,不需要再单独抓包验证——直接照抄 `feishu_project_webhook` 的字段路径：

```python
payload = body.get("payload") or body
work_item_id = payload.get("id")
project_key = payload.get("project_simple_name")
for node in payload.get("nodes") or []:
    for field in node.get("node_form") or []:
        if field.get("field_type_key") == "link":
            feishu_url = field["field_value"]
```

## 两轮技术评审 + 一轮需求影响面评审结论（全部核实为真，已吸收进下面的设计）

**第一轮技术评审**：消费者协程忘启动、CALM 必须同步改、任务结果不能扔掉、两个队列该合并、非 dict body 会 500、限流状态要防内存增长、删除范围要带上 `JIRA_WEBHOOK_SECRET`/`add_jira_comment`、Meego 评论接口约定不能想当然。

**第二轮技术评审（复查第一轮的修复本身）**，逐条核实为真：

1. **`functools.partial` 没有 `__eq__`**（实测验证：`functools.partial(f,1,2,3) == functools.partial(f,1,2,3)` → `False`）——现有单测 `tests/orchestrator/test_endpoints.py::test_trigger_test_case_generation_workflow` 断言 `mock_queue.put.assert_awaited_once_with((...))` 会直接断裂，**不是"重新跑一遍确认没回归"，是必须重写断言**（改成检查 `call_args.args[0].func`/`.args`，或者真的调用捕获到的 callable 再断言）
2. **`_run_requirement_review` 必须自己包一层 try/except**，模仿现有 `_run_pipeline` 的做法（`_pipeline_consumer` 现在是纯 `try/finally`，没有 `except`——因为 `_run_pipeline` 自己已经兜底捕获并打了带 `story_id` 的日志）。如果不这么做，失败时会丢失 `work_item_id` 上下文，而且 `_validate_task_status` 内部的 `_handle_exception` 已经调过一次 `_record_error`，外层队列的兜底 `except` 会再调一次，产生重复/更模糊的错误记录
3. **限流/去重状态需要一个 `asyncio.Lock()`**——没有锁的话，两个几乎同时到达的重复请求可能都在"检查未见过"这一步通过检查，双双入队，评审跑两次，正好是去重机制本来要防的问题。复用现有 `_stream_token_lock`（main.py 里已有的同款模式）
4. **CALM 改法要更精确**：
   - `rel-jira-webhook-orchestrator` 这条边和它的 `jira-webhook-hmac` 控制项是**同一个 JSON 对象**（控制项嵌在关系对象里），删关系对象控制项自动就没了，不是两步操作
   - `quaia.pattern.json` 里那条 `allOf` 断言必须**整条删掉**，不能改成指向新边——现有的飞书 webhook 姊妹边（`rel-feishu-project-webhook-orchestrator`）在 pattern.json 里根本没有对应断言，新边照这个先例，**不需要新加断言**
   - 新边**不能**照抄现成的 Feishu 边模板时连带复制 `orchestrator-api-key` 控制项——那样会在架构模型里谎报"这条边有鉴权"，跟"不鉴权"的真实设计矛盾
   - `jira-mcp-server` 这个节点**不能删**——`incident_creation` 还在真实用它，CALM 里虽然只有 `requirements-review-agent` 这条边引用了它，但代码层面 `incident_creation` 是独立实例化了自己的 `jira_mcp_server`，只是没被建模进 CALM，删节点会导致 CALM 校验过了但实际漏了一个真实依赖的文档。`rel-requirements-review-jira-mcp` 这条边要删或改指向（因为这个 agent 换成飞书 MCP 了），但 `jira-mcp-server` 节点本身、`rel-jira-mcp-jira` 都保留
5. **限流参数要给具体值**：全局速率上限定 **5/分钟**（不是之前随口说的 20，这个端点正常触发频率是"一个 PM 把一个 story 拖进一个状态"，不是高频 API，5/分钟已经比正常用量宽松很多，同时把攻击者能薅到的预算压到很低）；`_last_seen` 用**惰性清理**（每次调用时顺手清掉过期 key，不需要单独起一个周期性清理任务——用量小到不值得为此加后台任务）
6. **限流配置不该放 `RequirementsReviewAgentConfig`**——限流发生在 orchestrator 收到 webhook、还没派给任何 agent 之前，跟 agent 本身无关,应该放 `OrchestratorConfig`（跟 `API_KEY`、`TASK_EXECUTION_TIMEOUT` 放一起）。而且"复数命名风格"这个说法本身就不对——`config.py` 里的复数命名（`TERMINAL_STATUSES` 等）是因为那些是逗号分隔的列表配置，限流阈值是单个标量数字，没有复数先例可循
7. **日志要拆够细,不是两档,是四档**：格式解析失败（拿不到 `work_item_id`）、PRD 链接字段为空（这个之前漏提了,跟"非 dict body"是同一类"静默返回成功但啥也没干"的盲区）、按 work_item 去重丢弃（预期内、噪音，低优先级日志）、全局限流丢弃（可能是滥用信号，应该比去重丢弃更显眼）——四种情况分开打日志，不要混成"格式不对"和"限流丢弃"两档
8. `tests/agents/test_requirements_review.py` 里其实**没有 Jira 专属用例可删**（这个文件只有一个通用的 `test_requirements_review_agent_init`），需要做的是把 `monkeypatch.setattr(config, "JIRA_MCP_SERVER_URL", ...)` 改成 `FEISHU_MCP_SERVER_URL`——是改，不是删
9. `README.md`/`README.zh-CN.md` 里"### Triggering Workflows via Jira Webhooks"这个标题本身要改名——删完 Jira 那部分之后,这个标题底下就只剩飞书内容了,标题不改会很奇怪
10. 缺的 import：`functools`，以及 `collections.abc` 里的 `Callable`/`Awaitable`
11. `/story-ready-for-test-case-generation` 现有的入队调用本身也要跟着改成 `functools.partial(...)` 形式，不是只加新端点那一处

**需求影响面评审**：确认 `incident_creation`、`rag_sync_service` 两个暂缓阶段完全不受这次改动影响（各自独立实例化自己的 Jira 相关对象，没有共享代码路径），但额外发现两个删除范围的遗漏：

- `config.py` 的 `NEW_REQUIREMENTS_WEBHOOK_URL` 是这次改动导致的孤儿常量（唯一引用就是被删的端点），要一起删
- **`tests/smoke/test_smoke.py` 和 `tests/smoke/conftest.py` 里有硬编码引用 `/new-requirements-available` 的地方，其中 `AUTHENTICATED_WEBHOOKS`/`REQUIRED_FIELD_WEBHOOK_PATHS` 这两个共享参数化列表断言旧端点需要 API Key 鉴权——跟新端点"不鉴权"的设计正好相反，新端点不能塞进这两个列表里，需要专门写自己的负向测试**（无鉴权也能正常处理、限流丢弃、payload 格式不对丢弃）

**结合起来，smoke 测试这块是目前最大的一块硬工作**：`test_review_comment_reached_jira`、`test_agent_read_source_story_from_jira`、`test_requirements_review_webhook_accepted` 三个测试、`conftest.py` 里的 `requirements_review_response`/`webhook_responses` fixture 全部断言的是即将被替换掉的 Jira 边界，必须重写成断言飞书 MCP + Meego 评论 mock，不是"新增一个 Meego 断言 + 确认测试用例生成没回归"这么简单。

## 技术设计（最终版，已吸收两轮评审）

### 触发端点

新增 `POST /requirement-ready-for-review`（不鉴权），解析逻辑照抄锁定的字段路径，从原生 `WorkFlowNodeStatusEvent` 结构里提取 `work_item_id`/`project_key`/PRD 链接字段。解析前先判断 `isinstance(body, dict)`。四种"没往下走"的情况分别打日志（见上面第 7 点），对飞书一律返回 202（入队）或 204（其余三种）。

**已确认**：需求内容对应的是 story 工作项表单里一个链接类型字段，指向一篇独立的飞书文档，读取方式直接复用现成的 `feishu_get_doc_content`。

> 需要留意的运营层面的点（不是代码能解决的）：飞书项目的"状态进入"类自动化规则通常只在**第一次进入**该状态时触发一次。如果 PM 当时没填文档，之后补填了文档，**规则不会自动重新触发**（除非把状态挪出去再挪回来，或者飞书那边额外配一条"字段更新"类规则）。这个操作习惯需要跟 PM 那边对齐。

### 入队方式：合并进现有队列

```python
_pipeline_queue: asyncio.Queue[Callable[[], Awaitable[None]]] = asyncio.Queue()

async def _pipeline_consumer() -> None:
    """Sequentially consumes and executes queued jobs. Each job is expected to
    handle and log its own errors (mirroring _run_pipeline's existing behavior),
    so this outer except is a last-resort safety net, not the primary error path."""
    while True:
        job = await _pipeline_queue.get()
        try:
            await job()
        except Exception as e:
            _record_error(f"Queued job failed unexpectedly: {e}")
        finally:
            _pipeline_queue.task_done()
```

测试用例生成（现有调用点也要改成这个形式）：
`await _pipeline_queue.put(functools.partial(_run_pipeline, story_id, project_key, feishu_doc))`

需求评审：
`await _pipeline_queue.put(functools.partial(_run_requirement_review, work_item_id, project_key, feishu_doc))`

`_run_requirement_review` 自己包一层 try/except（模仿 `_run_pipeline`），内部调 `_send_task_to_agent` 后必须调 `_validate_task_status`，失败时用带 `work_item_id` 的消息调 `_record_error`。

限流 guard（`OrchestratorConfig` 下新增标量配置，不放 agent 配置里）：

```python
class _RequirementReviewGuard:
    """无鉴权端点的滥用防护：按 work_item_id 去重 + 全局限流，全内存实现。"""

    def __init__(self, dedup_window_seconds: int, global_max_per_minute: int):
        self._dedup_window = dedup_window_seconds
        self._global_max = global_max_per_minute
        self._last_seen: dict[str, float] = {}
        self._global_window: deque[float] = deque()
        self._lock = asyncio.Lock()  # 防止并发重复请求同时通过检查

    async def should_process(self, work_item_id: str) -> bool:
        async with self._lock:
            now = time.monotonic()
            # 惰性清理：数据量小，不值得为此起独立的周期性清理任务
            expired = [k for k, t in self._last_seen.items() if now - t > self._dedup_window]
            for k in expired:
                del self._last_seen[k]
            while self._global_window and now - self._global_window[0] > 60:
                self._global_window.popleft()
            if len(self._global_window) >= self._global_max:
                return False  # 全局限流丢弃：可能是滥用信号，日志级别应更高
            if work_item_id in self._last_seen:
                return False  # 去重丢弃：预期内噪音，低优先级日志
            self._global_window.append(now)
            self._last_seen[work_item_id] = now
            return True
```

默认值：去重窗口 300 秒，全局速率 **5/分钟**（不是宽松的 20——这个端点正常触发频率很低，没必要给潜在滥用留大预算）。

### Agent 改造

`agents/requirements_review/main.py` 复用现有 LLM 评审逻辑（system prompt、评审维度不变），只换输入输出两端：

- `jira_mcp_server` → `feishu_mcp_server`（跟 `test_case_generation` 同款）
- 去掉 `deps_type=JiraUserStory`——已核实这个 agent 的工具方法从来没读过 `ctx.deps`，是死代码,不用造一个飞书版的等价类型
- `_review_with_attachments(jira_issue_content, ...)` 重命名参数为 `requirement_doc_content`，内容来源改成 agent 自己调 `feishu_get_doc_content`（跟 `test_case_generation` 的模式一致），不是外部传入
- 新增 `add_requirement_review_comment` 方法，**直接放在 `RequirementsReviewAgent` 类上，不放 `AgentBase`**（`add_jira_comment` 只有这一个消费者，本来就是提前放错位置，这次不重复这个问题）
- System prompt 措辞：`"Jira issue"` → `"需求文档"`
- `tests/agents/test_requirements_review.py`：把 `monkeypatch.setattr(config, "JIRA_MCP_SERVER_URL", ...)` 改成 `FEISHU_MCP_SERVER_URL`（是改不是删，这个文件没有 Jira 专属用例）

### 评论写入

```
POST /open_api/{project_key}/work_item/comment/create
  {work_item_id, work_item_type_key: "story", content}
```

需要权限：`comment:comment.v1.create`。**这是唯一还带风险的环节**——调研内部姊妹项目（`herenjian/testplatform`）发现，他们对应的功能实际上从没用这条路（写死了但没调用过，大概率是权限批不下来或效果不好放弃了，改用飞书群机器人通知）。上线前必须先用测试插件验证这个权限真的能拿到、真的能写成功，不要等集成完了才发现走不通。如果批不下来，退回到"飞书群机器人通知"或"只在 Dashboard 展示"这两个已经在别的项目里验证过可行的方案。

## 前置条件（需要你去申请/确认，代码侧动不了）

- [ ] 在飞书项目后台给 story 工作流配一条自动化规则：进入"需求设计"状态 → 发送 HTTP 请求到 Orchestrator 新端点
- [x] 已确认操作习惯：进入"需求设计"状态时必须已经填好 PRD 链接，否则评审不会触发,且规则通常不会在补填后自动重跑（见上文"运营层面的点"）
- [ ] 新申请一个飞书项目插件（**不要复用姊妹项目 `testplatform` 里那套凭据**——那是另一个团队的插件身份，不该混用）
- [ ] 新插件申请 `comment:comment.v1.create` 权限，**并实际测试验证生效**

## 代码侧 TODO（前置条件满足后按顺序做）

- [ ] `orchestrator/main.py`：加 `functools`/`Callable`/`Awaitable` 的 import；泛化 `_pipeline_queue`/`_pipeline_consumer` 支持 callable job；`/story-ready-for-test-case-generation` 现有入队调用改成 `functools.partial` 形式
- [ ] `orchestrator/main.py`：新增 `_RequirementReviewGuard`（带锁、惰性清理、四档日志）+ `/requirement-ready-for-review` 端点 + `_run_requirement_review`（自己包 try/except + `_validate_task_status`）
- [ ] `orchestrator/main.py`：删除 `/new-requirements-available`、`_verify_jira_webhook_signature`、`_get_jira_issue_key_from_request`
- [ ] `config.py`：删除 `JIRA_WEBHOOK_SECRET`、`NEW_REQUIREMENTS_WEBHOOK_URL`；在 `OrchestratorConfig` 下新增限流用的标量配置
- [ ] `common/agent_base.py`：删除 `add_jira_comment`
- [ ] `common/services/meego_client.py` 加 `add_work_item_comment` 方法
- [ ] `agents/requirements_review/main.py`：按上面"Agent 改造"逐项改
- [ ] `tests/agents/test_requirements_review.py`：改 `monkeypatch` 目标（不是删用例）
- [ ] `tests/orchestrator/test_endpoints.py`：`test_trigger_test_case_generation_workflow` 的队列断言改成检查 `functools.partial` 的 `.func`/`.args`；删除 `test_review_jira_requirements_endpoint`/`test_review_jira_requirements_no_issue_key`
- [ ] `tests/orchestrator/test_security.py`：删除 `_verify_jira_webhook_signature` 相关测试
- [ ] 新增单测：guard 的去重/全局限流/惰性清理/并发锁/非 dict body、`_run_requirement_review` 的 `_validate_task_status` 调用与自包含错误处理、agent 的飞书评论写入逻辑
- [ ] `calm/architecture/quaia.arch.json`：删 `rel-jira-webhook-orchestrator`（连带的 `jira-webhook-hmac` 控制项一起没了）、删或改指向 `rel-requirements-review-jira-mcp`（**不要删 `jira-mcp-server` 节点和 `rel-jira-mcp-jira`**，`incident_creation` 还在用），加新的飞书需求评审 webhook 边（**不要**复制 Feishu 姊妹边的 `orchestrator-api-key` 控制项，描述里注明"无鉴权+限流兜底"这个设计）
- [ ] `calm/patterns/quaia.pattern.json`：把 `rel-jira-webhook-orchestrator`/`jira-webhook-hmac` 那条 `allOf` 断言整条删掉，**不要**加新断言指向新边（照抄现有飞书边先例，没有对应断言）
- [ ] `README.md`/`README.zh-CN.md`/`calm/README.md`：删掉 Jira webhook 相关行，"### Triggering Workflows via Jira Webhooks" 标题改名，加新端点说明
- [ ] `tests/smoke/test_smoke.py`：重写 `test_review_comment_reached_jira`/`test_agent_read_source_story_from_jira`/`test_requirements_review_webhook_accepted`，断言目标从 Jira mock 换成 Feishu MCP mock + 新增的 Meego 评论 mock；把 `/requirement-ready-for-review` 从 `AUTHENTICATED_WEBHOOKS`/`REQUIRED_FIELD_WEBHOOK_PATHS` 里排除（它就是设计成不鉴权的），单独写它自己的负向测试（无鉴权也正常处理/限流丢弃/payload 不对丢弃）
- [ ] `tests/smoke/conftest.py`：`requirements_review_response`/`webhook_responses`/`_WEBHOOKS` 里跟旧端点相关的部分同步改掉
- [ ] 确认队列合并后现有测试用例生成流程的单测+smoke 全部重新跑绿（这是碰到已有代码的改动，不能只看新增部分）
- [ ] 权限验证通过后再合入评论接口调用；批不下来就回退到群机器人通知或 Dashboard 展示

## 参考

- 现有可直接照抄的实现：`orchestrator/main.py` 的 `/story-ready-for-test-case-generation` 端点、`_pipeline_queue`/`_pipeline_consumer` 异步模式、`_stream_token_lock`（并发锁先例）、`scripts/feishu_mcp_server.py::feishu_get_doc_content`
- 内部姊妹项目 `git.corp.photontech.cc/herenjian/testplatform`：`prd_review/feishu_project.py::add_work_item_comment`（虽然没被真正调用过，但是接口调用方式的参照）、`prd_review/notifier.py`（评论权限批不下来时的退路——飞书群机器人通知）
