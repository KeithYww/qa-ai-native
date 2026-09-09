# 运行产物 OSS 持久化 TODO

## 背景

当前每次流水线运行产生的产物——生成的测试用例、分类结果、评审反馈，以及最近新增的执行 trace（`TaskRecord.trace_json`）——只存在 `orchestrator/models.py` 的 `TaskHistory` 内存 ring buffer 里：

- 进程重启即丢失
- `max_size=10000` 满了会驱逐最老记录（且驱逐时 `_tasks_by_id` 的清理是这次才补上的，历史上一直有泄露）
- 没有任何跨进程/跨部署的留档能力

项目已确定要用 Alibaba Cloud OSS 做测试用例归档（见 memory:`project_planned_knowledge_base_and_oss.md`），但代码库里**目前完全没有 OSS 集成**——没有 SDK 依赖、没有 bucket 配置、没有 client 代码。这份 TODO 是从零开始的落地方案。

## 业界调研结论（已完成，见对话记录）

调研了 Langfuse、Temporal、MLflow、AWS Step Functions 四个独立系统，全部收敛到同一个模式——**Claim Check（提取凭证）**：

- 大/原始内容写对象存储（S3/OSS）
- 快速可查询的那一层（数据库/内存结构）只留一个指向对象存储的引用
- 两层解耦：对象存储挂了不影响主流程,主流程的数据库/内存挂了不丢原始数据

这次的设计就是把这个模式套到 QuAIA 上，不是自创方案。

## 技术设计

### 归档范围

先覆盖这几类"产物"（对应用户说的"每次跑完生成的产物"）：

- 生成的测试用例（`GeneratedTestCases`）
- 分类结果（`ClassifiedTestCases`）
- 评审反馈（`TestCaseReviewFeedbacks`）
- 执行 trace（`trace_json`，脱敏后的）

> 待确认：trace 是否也要进 OSS，还是只做用例类产物的归档。trace 本身已经在 Dashboard 里能查（只是进程重启会丢），如果团队觉得"调试用完就完了不用留档"，可以先只归档测试用例三类，trace 归档留到后面按需再加。

### 存储 Key 设计

```
oss://{bucket}/runs/{story_id}/{task_id}/{artifact_type}.json
```

例：`oss://quaia-artifacts/runs/SMOKE-STORY-1/368f2c30-.../generated_test_cases.json`

- `story_id` 做一级目录，方便同一个需求的所有产物聚在一起看
- `artifact_type` ∈ `generated_test_cases` / `classified_test_cases` / `review_feedbacks` / `trace`

### 写入时机

复用现有的 hook 点，不新增编排逻辑：

- `orchestrator/main.py` 的 `_run_pipeline` 里，每步产出后（`_write_test_cases_to_feishu_project` 等调用点旁边）异步写一份到 OSS
- `_save_agent_trace_from_task`（如果 trace 也要归档）里追加一次 OSS 写入

**失败处理原则**：OSS 写入失败不能阻塞/拖垮主流程（飞书写回、Dashboard 展示都不依赖它）。做法参照现有 `_save_agent_usage_from_task`/`_save_agent_trace_from_task` 的 `try/except` + `logger.warning` 模式——记日志，不重试,不抛异常。

### 新增代码结构

- `common/services/oss_client.py`（新文件，参照现有 `common/services/meego_client.py` 之类的风格）：封装 `upload_json(key: str, data: dict) -> None`，内部用 `oss2` SDK
- `config.py` 新增 `OssConfig`：`BUCKET`、`ENDPOINT`（内网）、认证方式（RAM 角色优先，AK/SK 兜底）
- `orchestrator/models.py` 的 `TaskRecord` 可以加一个可选字段记录已归档的 OSS key（方便以后 Dashboard 加"查看归档"链接），但**不是这次的必做项**

### 依赖

新增 `oss2`（Alibaba Cloud 官方 Python SDK）到 `pyproject.toml` 的 `[project.dependencies]`，`uv lock` 生成锁文件。

### CALM 影响（这次真的要改，跟之前的 Agent Card/Trace 改动不一样）

OSS 是一个**真实的新增外部系统**,不是像 trace 那样只是既有通道上的新 artifact 类型。按 AGENTS.md 的要求,这次必须：

- `calm/architecture/quaia.arch.json` 新增 OSS 节点 + Orchestrator → OSS 的集成边
- 补上对应的安全控制（认证方式：RAM 角色 或 AK/SK）
- 如果这条边要被 CI 强制校验，`calm/patterns/quaia.pattern.json` 也要同步加断言

### Smoke 测试影响

按现有 `tests/smoke/mocks/` 的模式（参考 `qdrant_mock.py`）新增一个 OSS mock：

- 提供一个假的 `PutObject`/上传接口,记录收到的内容到 `/__recorded`
- 在现有的 test-case-generation 流程断言里补一条：确认生成结果确实写到了 mock OSS
- `docker-compose.smoke.yml` 加这个 mock 服务,orchestrator 的 OSS 配置指向它

## 前置条件（需要你去申请，代码侧动不了）

- [ ] 申请 OSS bucket（专用,不复用其他业务 bucket）
- [ ] 确认 Region 和 Orchestrator 所在 ECS/ACK 一致
- [ ] 申请访问方式：优先 ECS/ACK 实例 RAM 角色;否则申请仅限该 bucket 的 RAM 子账号 AK/SK
- [ ] 确认内网 Endpoint（不是公网）
- [ ] 确认 Bucket ACL 为 Private
- [ ] 确认是否需要生命周期策略（比如 90 天转 IA / 1 年后归档或删除）——这个在 Aliyun 控制台配置,不是代码层的事

## 代码侧 TODO（拿到资源后按顺序做）

- [ ] `pyproject.toml` 加 `oss2` 依赖 + `uv lock`
- [ ] `config.py` 加 `OssConfig`（bucket / endpoint / 认证配置）
- [ ] 新建 `common/services/oss_client.py`，封装上传逻辑,单测覆盖成功/失败两条路径
- [ ] `orchestrator/main.py` 的 `_run_pipeline` 接入 OSS 写入（生成/分类/评审三类产物,失败不阻塞主流程）
- [ ] （待确认后再做）trace 归档接入 `_save_agent_trace_from_task`
- [ ] `calm/architecture/quaia.arch.json` + `calm/patterns/quaia.pattern.json` 补 OSS 节点、边、安全控制
- [ ] `tests/smoke/mocks/` 新增 OSS mock,`docker-compose.smoke.yml` 接入,补充端到端断言
- [ ] 单测覆盖 `_run_pipeline` 里 OSS 写入失败时主流程仍然正常完成（回归测试,防止以后有人不小心让 OSS 写入变成阻塞式）

## 参考

- Claim Check 模式的四个业界实现参照：Langfuse（S3 缓冲 + ClickHouse 分析存储）、Temporal（External Storage + Codec Server claim 引用）、MLflow（Backend Store 存元数据 + Artifact Store 存文件,靠 `artifact_uri` 关联）、AWS Step Functions（大 payload 走 S3 引用而非直接塞进状态机）
- 现有代码里最接近的既有模式：`common/token_usage.py` + `orchestrator/models.py::update_usage`（小结构化数据直接存)对比 `common/trace_redaction.py` + `TaskRecord.trace_json`（较大数据,目前还在内存,是本 TODO 要接管的下一步)
