# E2E Run Findings — 2026-08-31 (run_local_pipeline)

## Run info

- Command: `env -u PORT uv run python -m scripts.run_local_pipeline "https://photonpay.feishu.cn/wiki/SKNKw8iili9Q2ike5f5cdqgPnhb"`
- Started ~16:54:29, generation finished 17:02:55, review finished 17:08:06.
- Output written: `output/20260831_170806.json`, `output/20260831_170806.md` (80 test cases).

## Anomaly 1 (real bug): review feedback lost — 0/80 test cases have `review_feedback` in the merged output

**Evidence**
- `logs/test_case_review.log` shows all 16 batches completed normally: `Reviewed batch N/16: 5 feedbacks.` (80 feedbacks total gathered inside `_review_test_cases_with_attachments`).
- Immediately after, `agent_base` logged a **WARNING**:
  > Agent returned incomplete result. LLM comments: ... `_review_test_cases_with_attachments` tool returned only a compact completion summary ('Reviewed 80 test cases.') ... no mechanism was available to me to retrieve that side-channel content directly, so the detailed per-test-case review feedback text was not accessible ... I cannot populate the `review_feedbacks` field ...
- Task still completed "successfully" (no exception), but the merged JSON output has `review_feedback: []` for all 80 entries — confirmed by checking `output/20260831_170806.json` directly.

**Root cause hypothesis**
`TestCaseReviewAgent.run()` (agents/test_case_review/main.py) does:
```python
_review_result_ctx.set(None)
msg = await super().run(received_message)
real_result = _review_result_ctx.get()
if real_result is not None:
    return new_text_message(text=real_result.model_dump_json(), ...)
return msg
```
and the tool does `_review_result_ctx.set(feedbacks)` at the end of `_review_test_cases_with_attachments`.

Unlike the generation agent's side-channel (`_generation_result_ctx`), which is set up in `run()` as a **mutable holder object** (`token = _generation_result_ctx.set(holder)`) that the tool only **mutates in place** (`_generation_result_ctx.get().append(...)`), the review agent's tool calls `.set()` again on the ContextVar itself. If pydantic-ai executes the tool call in a separate `asyncio.Task` (e.g. to support concurrent/parallel tool calling), that child task gets its own copy-on-write context — `.set()` inside it does **not** propagate back to the parent `run()` context. `_review_result_ctx.get()` back in `run()` then still sees the `None` set at the top, so `run()` falls through to `return msg`, i.e. the LLM's own (necessarily empty, since it has no access to the real data) structured output — which is exactly what the WARNING describes.

**Suggested fix direction (not yet applied)**: mirror the generation agent's pattern — set a mutable holder (e.g. `list[TestCaseReviewFeedbacks]`) via `token = _review_result_ctx.set(holder)` in `run()`, and have the tool `holder.append(feedbacks)` (or holder[0] = feedbacks) instead of calling `.set()` again.

## Anomaly 2: pipeline process doesn't exit after writing output

- Output files were written at 17:08:06, but the `python -m scripts.run_local_pipeline` process (PID 99880) was still alive and in `S` (sleeping) state at 17:11:37 (3+ min after completion), holding an `ESTABLISHED` TCP connection to the generation agent (port 8002).
- Suggests the `a2a` client's streaming connection/background task isn't being cleanly torn down after the response iterator is exhausted, keeping the event loop (and thus the process) alive past `asyncio.run(run_pipeline(...))` returning.
- Not blocking (output was already correct), but worth investigating if this script is used in CI/automation where a hang would matter.

## Anomaly 3 (stale, not from this run — for context only)

Older entries in the same log files (all from 14:06–15:34, i.e. **before** this run) show pre-existing issues from earlier local testing, not reproduced in this run:
- `feishu_get_doc_content` 400 Bad Request when a bare wiki token was passed directly to the docx `raw_content` API (this run used a full wiki URL, which worked).
- Test case classification: `pydantic_ai.exceptions.UnexpectedModelBehavior: Exceeded maximum retries (3) for output validation` on `ClassifiedTestCases`.
- Test case review: `AttributeError: 'NoneType' object has no attribute 'test_cases'`, `UsageLimitExceeded: Exceeded the total_tokens_limit of 1000000` (pre-dates the `TOTAL_TOKENS_LIMIT_PER_TASK = 2_000_000` bump in this branch), and one `address already in use` on port 8004 (leftover process from a previous local run).

These did not recur in the 16:54–17:08 run and are listed only so they aren't confused with fresh issues if the logs are re-read later.
