# 并发能力改进 TODO

## 背景

当前每个 Agent 是单进程单任务，Orchestrator Webhook handler 串行 `await` 整条 Pipeline（15-30 分钟），导致：
1. 飞书 Webhook 必然超时（飞书超时 5-10s，Pipeline 远超此值）
2. 多个故事同时触发时完全串行，第二个任务等第一个全部完成才开始

---

## 阶段一：解决 Webhook 超时（短期，优先）

**方案**：Orchestrator 引入 `asyncio.Queue` + consumer coroutine

- Webhook handler 立即返回 202，把任务放入队列
- 单个 consumer 后台顺序执行 Pipeline
- `MAX_CONCURRENT_PIPELINES=1`（单 Agent 实例阶段无需设更高）

**改动文件**：`orchestrator/main.py`

**关键实现要点**：
- `_background_tasks: set` 持有 task 引用防 GC
- consumer 内部异常必须捕获并记录，不能静默丢失
- 启动时通过 `lifespan` 或 `on_event("startup")` 创建 consumer task
- 可选：加 `/api/queue/status` 接口暴露队列深度（`_pipeline_queue.qsize()`）

**上线前注意**：
- 确认飞书自动化规则的重试历史，防止积压任务集中打 Photon API 触发 429
- 给 `_run_pipeline` 加 Photon API 429 指数退避重试

---

## 阶段二：单进程支持并发（中期）

**方案**：消灭 Agent 内的 `ContextVar` 共享状态，改用 pydantic-ai `deps` 传递

**根本问题**：
- `test_case_review/main.py` 的 `_test_cases_ctx` / `_review_result_ctx` 是模块级 ContextVar
- 多个并发请求会互相覆盖，被迫用 `-w 1` 单进程规避

**改动方向**：
- 把请求相关状态放进 Agent `deps_type`（RunContext），每个请求有独立实例
- 工具函数从 `ctx.deps` 取数据，不依赖模块级变量
- Orchestrator 调度从二值 AVAILABLE/BUSY 改为并发槽计数（`MAX_SLOTS_PER_AGENT`）

**改动文件**：
- `agents/test_case_review/main.py`（最复杂，有 ContextVar 侧信道）
- `common/agent_base.py`
- `orchestrator/main.py`（调度逻辑）

**效果**：单进程支持并发 3-5 个任务，无需加机器

---

## 阶段三：无状态化（长期，规模大了再做）

**方案**：Agent 进程完全无状态，中间状态外置到 Redis

- 所有请求上下文存 Redis，进程本身是纯函数
- 可任意开 `-w N` 多 worker 或横向加实例
- 对应业界主流架构（LangGraph Cloud、Bedrock Agents 等）

**触发条件**：团队规模 > 30 人，或同时并发任务 > 5 个时再评估

---

## 参考

- 业界模式：无状态 Agent（最主流）、Actor 模型（Temporal / Ray）
- 当前 ContextVar 用法是反模式：在无状态框架里强行有状态传递
- pydantic-ai 的 `deps` 机制天然支持请求级状态隔离，是正确的改造方向
