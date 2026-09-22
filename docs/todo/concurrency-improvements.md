# 并发能力改进 TODO

## 背景

当前每个 Agent 是单进程单任务，Orchestrator Webhook handler 串行 `await` 整条 Pipeline（15-30 分钟），导致：
1. 飞书 Webhook 必然超时（飞书超时 5-10s，Pipeline 远超此值）
2. 多个故事同时触发时完全串行，第二个任务等第一个全部完成才开始

当前架构限制下估算可支撑：**3-5 人测试团队**，双周迭代 15-20 个 story 分散触发场景下排队基本可接受。10 人以上团队在迭代末集中触发时会出现明显积压（每个 story 平均需要 6-15 分钟，串行等待最长可达 1 小时以上）。

---

## 阶段一：解决 Webhook 超时（已完成）

**方案**：Orchestrator 引入 `asyncio.Queue` + consumer coroutine

- Webhook handler 立即返回 202，把任务放入队列
- 单个 consumer 后台顺序执行 Pipeline
- `MAX_CONCURRENT_PIPELINES=1`（单 Agent 实例阶段无需设更高）

**状态**：已在 `feature/feishu-requirements-review` 分支完成并合入 main。

---

## 阶段二：单进程支持并发（中期）

**目标**：同一 Agent 进程支持并发执行 3-5 个任务，无需新增基础设施。

**根本问题**：两处模块级 ContextVar 在并发时会互相覆盖，被迫用 `_execute_lock` 串行化。

```python
# agents/test_case_review/main.py — 所有并发请求共享同一 ContextVar
_test_cases_ctx   = ContextVar(...)  # run() 写，tool 读
_doc_content_ctx  = ContextVar(...)  # run() 写，tool 读
_review_result_ctx = ContextVar(...) # tool 写，run() 读

# agents/test_case_generation/main.py
_generation_result_ctx = ContextVar(...)  # tool 写，run() 读
```

---

### 2.1 改动点全景

#### A. `agents/test_case_review/main.py`（改动最大，是核心阻塞点）

**现状**：3 个模块级 ContextVar 做输入传入和结果传出的侧信道。

**改造**：引入请求级状态容器，通过 pydantic-ai `deps` 机制传递。

```python
@dataclass(slots=True)
class _ReviewRunState:
    test_cases: list[TestCase]
    doc_content: str
    result: TestCaseReviewFeedbacks | None = None
```

- 删掉 `_test_cases_ctx`、`_doc_content_ctx`、`_review_result_ctx` 三个模块级变量
- `run()` 构造 `_ReviewRunState` 实例，调用 `agent.run(..., deps=state)`
- `_review_test_cases_with_attachments(ctx: RunContext[_ReviewRunState])` 从 `ctx.deps` 读输入、写结果
- 不再需要 `_test_cases_ctx.set()` / `_review_result_ctx.reset(token)` 的清理逻辑

#### B. `agents/test_case_generation/main.py`（改动较小）

**现状**：`_generation_result_ctx` 模块级 ContextVar，用 mutable list 绕过 asyncio Task 的 Context 隔离来传结果。

**改造**：同样换成 deps 模式。

```python
@dataclass(slots=True)
class _GenerationRunState:
    result: GeneratedTestCases | None = None
```

- 删掉 `_generation_result_ctx`
- `run()` 构造 `_GenerationRunState`，传给 `agent.run(deps=state)`
- `_generate_test_cases` tool 写 `ctx.deps.result`，`run()` 读 `state.result`

#### C. `common/agent_base.py`（中等改动）

**现状**：`_get_agent_execution_result` 不支持传 deps；`latest_token_usage`、`latest_trace`、`latest_received_message` 是实例属性，并发时会被覆盖。

**改造**：
- `_get_agent_execution_result(received_request, deps=None)` 加可选参数，透传给 `agent.run(deps=deps)`
- 把 `latest_token_usage`、`latest_trace`、`latest_received_message` 从实例属性改为请求级局部变量，通过返回值或 deps 传出去，不再挂在 `self` 上

#### D. `common/agent_executor.py`（改动较多，有隐藏问题）

**现状**：`_execute_lock` 串行化所有请求；`activity_queue` 是 agent 单例的；日志 handler 通过 `root_logger.addHandler` 全局注册。

**隐藏问题**：

1. **`activity_queue` 并发串台**：`agent_base.py:89` 每个 agent 实例只有一个 `_activity_queue`，并发时任务 A 的活动描述可能被发到任务 B 的 `updater`，导致监控数据错乱。

   **具体路由方案**：在 `common/streaming.py` 里新增一个 ContextVar（与 `current_log_handler` 对称）：

   ```python
   current_activity_queue: ContextVar[asyncio.Queue | None] = ContextVar(
       "current_activity_queue", default=None
   )
   ```

   executor 在调用 `agent.run()` 前创建一个 per-request 的 `asyncio.Queue` 并绑定到 ContextVar；`agent_base.py` 的 `report_activity` 方法优先读这个 ContextVar，有值就往里写，没值降级写 `self._activity_queue`（向后兼容单任务场景）。

   ```python
   async def report_activity(self, description: str) -> None:
       q = current_activity_queue.get(None) or self._activity_queue
       try:
           q.put_nowait(description)
       except asyncio.QueueFull:
           logger.debug("Activity queue full; dropping: %s", description)
   ```

   `flush_activity_loop` 绑定到当前请求的 per-request queue，不再读 `self.agent.activity_queue`。

2. **日志串台（最严重）**：`root_logger.addHandler(log_handler)` 是全局操作，并发时多个 per-task handler 同时挂在根 logger 上，每个 handler 都会收到所有任务产生的日志。需要改用 `current_log_handler` ContextVar 做路由（见改动 E），这样即使多个 handler 同时挂着，`emit()` 内部会过滤掉不属于当前 Task 的日志。

   > **注意**：这个路由方案的前提假设是所有日志调用都发生在 asyncio 协程（Task）里，不在 `threading.Thread` 里。asyncio Task 创建时会复制父 Task 的 Context，pydantic-ai 内部的子 Task 会继承 `current_log_handler` 绑定，所以能正确路由。如果未来引入 `threading.Thread` 调用，那个线程里 `current_log_handler.get(None)` 会返回 None，日志会被丢弃——这是可接受的取舍，但**必须在代码注释里写明这个假设**，防止未来踩坑。

3. **`latest_token_usage` / `latest_trace` 读取**：并发时任务 A 写入后被任务 B 覆盖，executor 在 finally 里读 `self.agent.latest_token_usage` 可能拿到错误任务的值。随 agent_base.py 的改造一起解决（改为从请求级取）。

**改造**：
- 去掉 `_execute_lock`（最后一步，在以上所有状态安全改造完成并测试绿后再动）
- executor 在调用 `agent.run()` 前创建 per-request `asyncio.Queue`，通过 `current_activity_queue` ContextVar 绑定；`flush_activity_loop` 读这个 per-request queue
- `AgentLogCaptureHandler.emit()` 加 ContextVar 路由（见改动 E）

#### E. `common/agent_log_capture.py`（漏评估，需新增逻辑）

**现状**：`emit()` 直接存进 `self._buffer`，没有路由逻辑。

**改造**：`emit()` 读 `current_log_handler.get()`，只在 `get()` 结果是自身时才存入 buffer，否则忽略。这样即使多个 handler 同时挂着，每条日志只进入当前 asyncio Task 对应的 handler。

```python
def emit(self, record: logging.LogRecord) -> None:
    # 只捕获当前 asyncio Task 上下文绑定到本 handler 的日志
    if current_log_handler.get(None) is not self:
        return
    try:
        log_entry = self.format(record)
        with self._lock:
            self._buffer.append(log_entry)
            self._emitted_total += 1
    except Exception:
        self.handleError(record)
```

#### F. `orchestrator/models.py` → `AgentRegistry`（漏评估，阻塞性改动）

**现状**：`AgentStatus` 二值（AVAILABLE/BUSY），`_current_tasks: dict[str, str]`（单值）。

**改造**：
- 引入 `MAX_SLOTS_PER_AGENT` 配置（默认 3）
- `_busy_slots: dict[str, int]` 替代二值状态，记录每个 agent 当前占用的并发槽数
- `_current_tasks: dict[str, list[str]]` 改为多值
- `get_available_agents()` 改为"busy_slots < max_slots"的 agent
- `update_status(BUSY)` → 槽位 +1；`update_status(AVAILABLE)` → 槽位 -1
- **不改 `AgentStatus` 枚举的语义**：BUSY 表示"至少有一个任务在跑"，AVAILABLE 表示"有空余槽位"，对外接口向后兼容

#### G. `orchestrator/main.py` → `reserve_agent_waiting_if_needed`（中等改动）

**现状**：等待循环判断 `AgentStatus.AVAILABLE`，选中后立即改 BUSY。

**改造**：等待条件改为"目标 agent 有空余槽位"，原子地递增槽位计数。取消"BUSY 了就跳过"的逻辑，改为"槽位满了才跳过"。

#### H. `orchestrator/ui/src/components/AgentGrid.tsx`（并发上线后联动改）

**现状**：每个 agent 卡片只展示一个 `current_task`。

**改造**（阶段二上线后才做）：`current_task` API 字段改为 `current_tasks: list`，前端展示多个并发任务的卡片或计数徽章。

**当前不需要动**：在去掉 `_execute_lock` 之前，单任务展示是准确的。

#### I. Dashboard 成本列（独立缺陷，与并发无关）

**现状**：`config.py:133` 的 `MODEL_PRICING` 是空字典，`cost_usd` 永远是 `None`，TaskList 的 Cost 列全是 `-`。

**修复**：填入 Photon API relay 的实际定价，或暂时隐藏该列，避免误导用户。

---

### 2.2 改造顺序

> 步骤 1-5 完成后，并发行为与现在完全一致（`_execute_lock` 仍在，仍然串行）。但内部数据传递路径已从 ContextVar 重构为请求级 deps，**这是真实的行为变化，必须在步骤 6 做全量功能验证**，不能因为"并发行为没变"而跳过验证。步骤 1-5 期间任何时候可以安全回滚。

```
1. test_case_review/main.py   — ContextVar → deps，单测跑绿
2. test_case_generation/main.py — ContextVar → deps，单测跑绿
3. agent_base.py              — 加 deps 参数；latest_xxx 从实例属性移走
4. common/streaming.py        — 新增 current_activity_queue ContextVar
   agent_log_capture.py       — emit() 加 ContextVar 路由 + 线程假设注释
   agent_base.py              — report_activity 改为优先读 current_activity_queue
5. agent_executor.py          — per-request activity queue 创建与绑定；
                                 flush_activity_loop 读 per-request queue
6. 全量单测 + smoke suite 跑绿
   （_execute_lock 仍在；验证内部数据流重构后功能正确性，不只是"跑过"）
7. orchestrator/models.py     — AgentRegistry 改为槽位计数
8. orchestrator/main.py       — 等待循环改为槽位判断
9. agent_executor.py          — 去掉 _execute_lock（打开并发开关）
   ← 单独一个 commit，方便回滚
10. 压测：3 个并发任务跑完整 pipeline，验证无状态污染、无日志串台、
    activity 事件路由到正确任务
11. AgentGrid.tsx             — current_task 改为 current_tasks 列表展示
```

---

### 2.3 预期效果与约束

- 单进程支持并发 3 个 pipeline（可通过 `MAX_SLOTS_PER_AGENT` 调整）
- 不引入任何新基础设施（无 Redis、无消息队列）
- 适合 5-10 人测试团队，双周迭代高峰期排队基本消除
- **不适合** 10 人以上团队或高频触发场景（见阶段三）

---

## 阶段三：无状态化（长期，规模大了再做）

**方案**：Agent 进程完全无状态，中间状态外置到 Redis

- 所有请求上下文存 Redis，进程本身是纯函数
- 可任意开 `-w N` 多 worker 或横向加实例
- 对应业界主流架构（LangGraph Cloud、Bedrock Agents 等）
- 运维成本：+1 个 Redis 实例（建议用阿里云托管 Redis，省自运维）

**触发条件**：团队规模 > 30 人，或同时并发任务 > 5 个时再评估

---

## 参考

- 业界模式：无状态 Agent（最主流）、Actor 模型（Temporal / Ray）
- 当前 ContextVar 用法是反模式：在无状态框架里强行有状态传递
- pydantic-ai 的 `deps` 机制天然支持请求级状态隔离，是正确的改造方向
- 日志路由问题：标准做法是 logging Filter 或 ContextVar 路由，不是全局 addHandler
