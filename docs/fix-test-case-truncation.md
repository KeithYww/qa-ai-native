# Fix: TestCaseGenerationAgent Output Truncation

## Problem

The outer orchestrator LLM (`qwen3.7-plus`) truncates generated test cases from 27 to 10 during
structured output serialization. Sub-agents correctly produce all 27 test cases, but pydantic-ai
requires the outer LLM to re-serialize the tool return value into `output_type=GeneratedTestCases`.
The LLM silently drops items during this relay.

## Root Cause

`_generate_test_cases` returns `GeneratedTestCases` (a large BaseModel). pydantic-ai serializes
this as a tool result (JSON string in `ToolReturnPart.content`) and passes it back to the outer LLM,
which must then call `final_result({test_cases: [...]})` to produce the structured output. With 27
test cases the JSON payload exceeds what `ORCHESTRATOR_THINKING_LEVEL="low"` reliably re-serializes.

## Fix: ContextVar Side-Channel

Store the real result in a `ContextVar` inside the tool function so `run()` can return it directly,
bypassing the LLM re-serialization step entirely.

### Why `ContextVar` (not instance variable)

`TestCaseGenerationAgent` is a module-level singleton. Concurrent requests share the same instance.
An instance variable (`self._last_result`) would be overwritten between the `set` and `get` points
because `asyncio.gather` inside `create_test_cases_from_steps` and `generate_test_steps` creates
cooperative yield points between concurrent requests.

`contextvars.ContextVar` is copy-on-write per asyncio `Task`: each HTTP request runs in its own
task, so reads and writes are isolated without locks.

### Why `super().run()` (not duplicate base-class logic)

Overriding `run()` to call `_get_agent_execution_result()` directly would silently drop:
- `self.latest_received_message = received_message` (used by executor and tools)
- `_capture_token_usage(result)` (token reporting to dashboard)
- `_log_llm_comments_if_result_incomplete(result.output)` (diagnostics)
- The full `try/except` → `AgentRuntimeError` block (A2A error protocol)

Delegating to `super().run()` keeps all of this and only replaces the final return value.

### What happens with `output_type`

The outer orchestrator's `output_type` is changed from `GeneratedTestCases` to `str`. The LLM
only needs to call `final_result("Successfully generated N test cases.")`, which it can trivially
do. This eliminates the `output_retries` risk entirely.

`super().run()` returns a `Message` containing that summary string. Our `run()` override discards
it and substitutes the real `GeneratedTestCases` result captured by the ContextVar.

## Files Changed

| File | Change |
|------|--------|
| `agents/test_case_generation/main.py` | Change outer orchestrator `output_type` from `GeneratedTestCases` to `str`; add module-level `ContextVar`; change `_generate_test_cases` return to `str` + set ContextVar; override `run()` to substitute ContextVar result |
| `tests/agents/test_test_case_generation.py` | Update `test_generate_test_cases_flow`: assert return is `str`, assert ContextVar is set; add `test_run_returns_full_result_via_context_var` |

## Diff (main.py)

```diff
+from contextvars import ContextVar
+from a2a.helpers import new_text_message
+from a2a.types import Message

+_generation_result_ctx: ContextVar[GeneratedTestCases | None] = ContextVar(
+    "_generation_result_ctx", default=None
+)

 class TestCaseGenerationAgent(AgentBase):

-    async def _generate_test_cases(
-        self, requirement_doc_content: str, attachment_paths: list[str]
-    ) -> GeneratedTestCases:
+    async def _generate_test_cases(
+        self, requirement_doc_content: str, attachment_paths: list[str]
+    ) -> str:
         ...
-        return generated_test_cases
+        _generation_result_ctx.set(generated_test_cases)
+        return f"Successfully generated {len(generated_test_cases.test_cases)} test cases."

+    async def run(self, received_message: Message) -> Message:
+        token = _generation_result_ctx.set(None)
+        try:
+            result_message = await super().run(received_message)
+            generation_result = _generation_result_ctx.get()
+            if generation_result is None:
+                return result_message
+            context_id = getattr(received_message, "context_id", None)
+            task_id = getattr(received_message, "task_id", None)
+            return new_text_message(
+                text=generation_result.model_dump_json(),
+                context_id=context_id,
+                task_id=task_id,
+            )
+        finally:
+            _generation_result_ctx.reset(token)
```

## Test Cases to Add / Update

### Update: `test_generate_test_cases_flow`

- Assert return value is `str` (not `GeneratedTestCases`)
- Assert `_generation_result_ctx.get()` equals the mock result

### New: `test_run_returns_full_result_via_context_var`

1. Mock `super().run()` to return a message built from truncated `GeneratedTestCases` (5 items)
2. Set `_generation_result_ctx` to a `GeneratedTestCases` with 10 items (simulating the tool having
   already set it during the agent run)
3. Call `agent.run(mock_message)`
4. Assert returned message body parses to `GeneratedTestCases` with 10 items (ContextVar wins)

### New: `test_run_falls_back_when_tool_not_called`

1. Mock `super().run()` to return a message built from `GeneratedTestCases` with 3 items
2. Do NOT set `_generation_result_ctx` (leave at default `None`)
3. Call `agent.run(mock_message)`
4. Assert returned message equals `super().run()` output (fallback path)

## Acceptance Criteria

- `uv run pytest tests/agents/test_test_case_generation.py -v` passes
- E2E pipeline (`env -u PORT uv run python -m scripts.run_local_pipeline <feishu_url>`) reports the
  same count in generation output as the internal log line `Generated N test cases in total.`
