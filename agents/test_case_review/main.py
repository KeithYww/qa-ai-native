# SPDX-FileCopyrightText: 2025-2026 Taras Paruta (partarstu@gmail.com)
#
# SPDX-License-Identifier: AGPL-3.0-only

import asyncio
import json
from contextvars import ContextVar
from itertools import batched
from typing import TYPE_CHECKING

from a2a.helpers import get_message_text, new_text_message
from a2a.types import AgentSkill, Message
from pydantic_ai import RunContext
from pydantic_ai.settings import ThinkingLevel

import config
from agents.test_case_review.prompt import TestCaseReviewSystemPrompt, TestCaseReviewWithAttachmentsPrompt
from common import utils
from common.agent_base import MCP_SERVER_ATTACHMENTS_FOLDER_PATH, AgentBase
from common.custom_llm_wrapper import CustomLlmWrapper
from common.llm_provider import get_model
from common.models import TestCaseReviewFeedbacks, TestCaseReviewRequest

if TYPE_CHECKING:
    from pydantic_ai.messages import BinaryContent

logger = utils.get_logger("test_case_review_agent")

_test_cases_ctx: ContextVar[list] = ContextVar("_review_test_cases_ctx", default=[])
_doc_content_ctx: ContextVar[str] = ContextVar("_review_doc_content_ctx", default="")
_review_result_ctx: ContextVar[list[TestCaseReviewFeedbacks]] = ContextVar("_review_result_ctx")


def _extract_doc_content_from_text(text: str) -> str:
    """Extracts the requirement document content from the orchestrator message."""
    marker = "Requirement document content:\n"
    idx = text.find(marker)
    if idx == -1:
        return text
    content = text[idx + len(marker):]
    # Strip the test cases block that may follow
    tc_marker = "\n\nTest cases:\n"
    tc_idx = content.find(tc_marker)
    return content[:tc_idx] if tc_idx != -1 else content


def _parse_test_cases_from_text(text: str) -> list:
    """Extracts test cases from the first JSON object that contains a 'test_cases' key."""
    pos = 0
    while pos < len(text):
        start = text.find("{", pos)
        if start == -1:
            break
        depth = 0
        for i, c in enumerate(text[start:], start):
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(text[start : i + 1])
                        if "test_cases" in data:
                            return TestCaseReviewRequest.model_validate(data).test_cases
                    except (json.JSONDecodeError, ValueError):
                        pass
                    pos = i + 1
                    break
        else:
            break
    return []


class TestCaseReviewAgent(AgentBase):
    __test__ = False

    def __init__(self):
        # Create a sub-agent for reviewing with attachments
        self.review_agent = CustomLlmWrapper.create_agent(
            model_name=get_model(
                config.TestCaseReviewAgentConfig.MODEL_NAME, config.TestCaseReviewAgentConfig.FALLBACK_MODEL_NAME
            ),
            output_type=TestCaseReviewFeedbacks,
            system_prompt=TestCaseReviewWithAttachmentsPrompt().get_prompt(),
            name="review_test_cases_with_attachments",
            thinking_level=config.TestCaseReviewAgentConfig.THINKING_LEVEL,
            max_tokens=config.TestCaseReviewAgentConfig.MAX_TOKENS,
        )

        instruction_prompt = TestCaseReviewSystemPrompt(
            attachments_remote_folder_path=MCP_SERVER_ATTACHMENTS_FOLDER_PATH,
            batch_size=config.TestCaseReviewAgentConfig.TEST_CASE_REVIEW_BATCH_SIZE,
        )
        super().__init__(
            agent_name=config.TestCaseReviewAgentConfig.OWN_NAME,
            base_url=config.AGENT_BASE_URL,
            port=config.TestCaseReviewAgentConfig.PORT,
            external_port=config.TestCaseReviewAgentConfig.EXTERNAL_PORT,
            protocol=config.TestCaseReviewAgentConfig.PROTOCOL,
            model_name=config.TestCaseReviewAgentConfig.MODEL_NAME,
            fallback_model_name=config.TestCaseReviewAgentConfig.FALLBACK_MODEL_NAME,
            deps_type=TestCaseReviewRequest,
            output_type=TestCaseReviewFeedbacks,
            instructions=instruction_prompt.get_prompt(),
            mcp_servers=[],
            description="Agent which reviews generated test cases for coherence, redundancy, and effectiveness.",
            tools=[self._review_test_cases_with_attachments],
        )

    def get_thinking_level(self) -> ThinkingLevel:
        return config.TestCaseReviewAgentConfig.THINKING_LEVEL

    def get_max_requests_per_task(self) -> int:
        return config.TestCaseReviewAgentConfig.MAX_REQUESTS_PER_TASK

    def get_total_tokens_limit(self) -> int:
        return config.TestCaseReviewAgentConfig.TOTAL_TOKENS_LIMIT_PER_TASK

    def get_max_tokens(self) -> int | None:
        # The main orchestrating LLM's output is discarded in favour of the ContextVar
        # side-channel result, so a small limit is sufficient and leaves more room for input.
        return 4096

    def get_skills(self) -> list[AgentSkill]:
        return [
            AgentSkill(
                id="review-test-cases",
                name="Review test cases",
                description="Reviews generated test cases for coherence, redundancy, and effectiveness.",
                tags=["qa", "test-case-review"],
            )
        ]

    async def run(self, received_message: Message) -> Message:
        text = get_message_text(received_message)
        test_cases = _parse_test_cases_from_text(text)
        context_id = getattr(received_message, "context_id", None)
        task_id = getattr(received_message, "task_id", None)
        if test_cases:
            _test_cases_ctx.set(test_cases)
            doc_content = _extract_doc_content_from_text(text)
            if doc_content:
                _doc_content_ctx.set(doc_content)
            # Pass only a minimal instruction to the main LLM — both test cases and
            # doc content are in ContextVars and do not need to travel through the LLM.
            received_message = new_text_message(
                text="Review the test cases using the requirement document.",
                context_id=context_id,
                task_id=task_id,
            )
        else:
            logger.warning("Could not extract test cases from review request message.")
        holder: list[TestCaseReviewFeedbacks] = []
        token = _review_result_ctx.set(holder)
        try:
            msg = await super().run(received_message)
            if not holder:
                return msg
            return new_text_message(text=holder[0].model_dump_json(), context_id=context_id, task_id=task_id)
        finally:
            _review_result_ctx.reset(token)

    async def _review_test_cases_with_attachments(self, ctx: RunContext[TestCaseReviewRequest]) -> str:
        """
        Reviews all test cases from the request in batches, using the requirement document.

        All inputs (test cases, doc content) are read from ContextVars set in run().

        Returns:
            A compact completion summary (full feedbacks are stored in the ContextVar side-channel).
        """
        test_cases = _test_cases_ctx.get()
        if not test_cases:
            raise RuntimeError("No test cases available for review.")
        requirement_doc_content = _doc_content_ctx.get()
        attachments_content = {}
        batch_size = config.TestCaseReviewAgentConfig.TEST_CASE_REVIEW_BATCH_SIZE
        batches = list(batched(test_cases, batch_size, strict=False))
        logger.info(f"Reviewing {len(test_cases)} test cases in {len(batches)} batch(es) of up to {batch_size}.")
        tasks = [
            self._review_batch(batch, i + 1, len(batches), requirement_doc_content, attachments_content)
            for i, batch in enumerate(batches)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_feedbacks = []
        llm_comments = []
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                raise RuntimeError(f"Review failed for batch {i + 1}: {result}") from result
            all_feedbacks.extend(result.review_feedbacks)
            if result.llm_comments:
                llm_comments.append(result.llm_comments)
        feedbacks = TestCaseReviewFeedbacks(
            review_feedbacks=all_feedbacks,
            llm_comments="\n".join(llm_comments) or None,
        )
        _review_result_ctx.get().append(feedbacks)
        return f"Review completed. Reviewed {len(all_feedbacks)} test cases."

    async def _review_batch(
        self,
        batch,
        batch_number: int,
        total_batches: int,
        requirement_doc_content: str,
        attachments_content: dict,
    ) -> TestCaseReviewFeedbacks:
        user_message_parts: list[str | BinaryContent] = [
            f"Requirement document content:\n{requirement_doc_content}",
            f"Test Cases to Review:\n{json.dumps([tc.model_dump() for tc in batch], ensure_ascii=False)}",
        ]
        if attachments_content:
            for filename, binary_content in attachments_content.items():
                user_message_parts.append(f"Attachment: {filename}")
                user_message_parts.append(binary_content)
        logger.info(f"Starting review of batch {batch_number}/{total_batches}.")
        result = await self.review_agent.run(user_message_parts)
        feedbacks: TestCaseReviewFeedbacks = result.output
        logger.info(
            f"Reviewed batch {batch_number}/{total_batches}: {len(feedbacks.review_feedbacks)} feedbacks."
        )
        return feedbacks

agent = TestCaseReviewAgent()
app = agent.a2a_server

if __name__ == "__main__":
    agent.start_as_server()
