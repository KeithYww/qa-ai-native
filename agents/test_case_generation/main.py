# SPDX-FileCopyrightText: 2025-2026 Taras Paruta (partarstu@gmail.com)
#
# SPDX-License-Identifier: AGPL-3.0-only

import asyncio
from contextvars import ContextVar
from itertools import batched

from a2a.helpers import new_text_message
from a2a.types import AgentSkill, Message
from pydantic_ai.mcp import MCPServerSSE
from pydantic_ai.messages import BinaryContent
from pydantic_ai.settings import ThinkingLevel

import config
from agents.test_case_generation.prompt import (
    AcExtractionPrompt,
    TcGenerationPrompt,
    TestCaseGenerationSystemPrompt,
)
from common import utils
from common.agent_base import AgentBase
from common.custom_llm_wrapper import CustomLlmWrapper
from common.llm_provider import get_model
from common.models import (
    AcceptanceCriteriaItem,
    AcceptanceCriteriaList,
    GeneratedTestCases,
    JiraUserStory,
)
from common.services.test_management_system_client_provider import get_test_management_client

logger = utils.get_logger("test_case_generation_agent")
feishu_mcp_server = MCPServerSSE(url=config.FEISHU_MCP_SERVER_URL, timeout=config.MCP_SERVER_TIMEOUT_SECONDS)
_generation_result_ctx: ContextVar[list[GeneratedTestCases]] = ContextVar("_generation_result_ctx")


class TestCaseGenerationAgent(AgentBase):
    __test__ = False

    def __init__(self):
        self.ac_extraction_prompt = AcExtractionPrompt()
        self.tc_generation_prompt = TcGenerationPrompt()

        model_name = get_model(
            config.TestCaseGenerationAgentConfig.MODEL_NAME, config.TestCaseGenerationAgentConfig.FALLBACK_MODEL_NAME
        )

        self.ac_extractor_agent = CustomLlmWrapper.create_agent(
            model_name=model_name,
            output_type=AcceptanceCriteriaList,
            system_prompt=self.ac_extraction_prompt.get_prompt(),
            toolsets=[feishu_mcp_server],
            name="ac_extractor",
            thinking_level=config.TestCaseGenerationAgentConfig.AC_EXTRACTOR_THINKING_LEVEL,
            max_tokens=config.TestCaseGenerationAgentConfig.AC_EXTRACTOR_MAX_TOKENS,
        )

        self.test_case_generator_agent = CustomLlmWrapper.create_agent(
            model_name=model_name,
            output_type=GeneratedTestCases,
            system_prompt=self.tc_generation_prompt.get_prompt(),
            name="test_case_generator",
            thinking_level=config.TestCaseGenerationAgentConfig.TC_GENERATOR_THINKING_LEVEL,
            max_tokens=config.TestCaseGenerationAgentConfig.TC_GENERATOR_MAX_TOKENS,
        )

        instruction_prompt = TestCaseGenerationSystemPrompt()
        super().__init__(
            agent_name=config.TestCaseGenerationAgentConfig.OWN_NAME,
            base_url=config.AGENT_BASE_URL,
            port=config.TestCaseGenerationAgentConfig.PORT,
            external_port=config.TestCaseGenerationAgentConfig.EXTERNAL_PORT,
            protocol=config.TestCaseGenerationAgentConfig.PROTOCOL,
            model_name=config.TestCaseGenerationAgentConfig.MODEL_NAME,
            fallback_model_name=config.TestCaseGenerationAgentConfig.FALLBACK_MODEL_NAME,
            output_type=str,
            instructions=instruction_prompt.get_prompt(),
            mcp_servers=[feishu_mcp_server],
            deps_type=JiraUserStory,
            description="Agent which generates test cases based on requirement documents.",
            tools=[self._generate_test_cases],
        )

    def get_thinking_level(self) -> ThinkingLevel:
        return config.TestCaseGenerationAgentConfig.ORCHESTRATOR_THINKING_LEVEL

    def get_max_requests_per_task(self) -> int:
        return config.TestCaseGenerationAgentConfig.MAX_REQUESTS_PER_TASK

    def get_skills(self) -> list[AgentSkill]:
        return [
            AgentSkill(
                id="generate-test-cases",
                name="Generate test cases",
                description="Generates test cases based on requirement documents.",
                tags=["qa", "test-case-generation"],
            )
        ]

    async def _generate_test_cases(
        self, requirement_doc_content: str, attachment_paths: list[str]
    ) -> str:
        """
        Generates test cases based on the requirement document content and attachments.

        Args:
            requirement_doc_content: The whole content of the requirement document.
            attachment_paths: List of file paths to the downloaded attachments.

        Returns:
            Summary string; full result is stored in _generation_result_ctx for run() to retrieve.
        """
        attachments_content = self._fetch_attachments(attachment_paths)
        extracted_acceptance_criteria = await self.extract_acceptance_criteria(
            attachments_content, requirement_doc_content
        )
        generated_test_cases = await self.generate_test_cases_from_acs(
            extracted_acceptance_criteria, requirement_doc_content
        )
        _generation_result_ctx.get().append(generated_test_cases)
        return f"Successfully generated {len(generated_test_cases.test_cases)} test cases."

    async def run(self, received_message: Message) -> Message:
        holder: list[GeneratedTestCases] = []
        token = _generation_result_ctx.set(holder)
        try:
            result_message = await super().run(received_message)
            if not holder:
                return result_message
            context_id = getattr(received_message, "context_id", None)
            task_id = getattr(received_message, "task_id", None)
            return new_text_message(
                text=holder[0].model_dump_json(),
                context_id=context_id,
                task_id=task_id,
            )
        finally:
            _generation_result_ctx.reset(token)

    async def _generate_test_cases_for_ac_batch(
        self,
        batch: tuple[AcceptanceCriteriaItem, ...],
        all_acs: AcceptanceCriteriaList,
        requirement_doc_content: str,
        batch_number: int,
        total_batches: int,
    ) -> GeneratedTestCases:
        """Generates test cases for a batch of ACs in a single LLM call."""
        user_message = (
            f"Requirement document content:\n{requirement_doc_content}\n\n"
            f"All acceptance criteria (for naming context):\n{all_acs.model_dump_json()}\n\n"
            f"Acceptance criteria items to process in this batch:\n"
            f"{AcceptanceCriteriaList(items=list(batch)).model_dump_json()}"
        )
        result = await self.test_case_generator_agent.run(user_message)
        batch_result: GeneratedTestCases = result.output
        logger.info(
            f"Generated {len(batch_result.test_cases)} test cases in batch {batch_number}/{total_batches}."
        )
        return batch_result

    async def generate_test_cases_from_acs(
        self,
        extracted_acceptance_criteria: AcceptanceCriteriaList,
        requirement_doc_content: str,
    ) -> GeneratedTestCases:
        """Generates all test cases from ACs using batched LLM calls."""
        batch_size = config.TestCaseGenerationAgentConfig.AC_BATCH_SIZE
        batches = list(batched(extracted_acceptance_criteria.items, batch_size, strict=False))
        logger.info(
            f"Generating test cases for {len(extracted_acceptance_criteria.items)} ACs "
            f"in {len(batches)} batch(es) of up to {batch_size}."
        )
        sem = asyncio.Semaphore(config.TestCaseGenerationAgentConfig.LLM_CONCURRENCY_LIMIT)

        async def _run_batch(batch: tuple[AcceptanceCriteriaItem, ...], idx: int) -> GeneratedTestCases:
            async with sem:
                return await self._generate_test_cases_for_ac_batch(
                    batch, extracted_acceptance_criteria, requirement_doc_content, idx + 1, len(batches)
                )

        tasks = [_run_batch(batch, i) for i, batch in enumerate(batches)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        test_cases, llm_comments = [], []
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                raise RuntimeError(f"TC generation failed for batch {i + 1}: {result}") from result
            test_cases.extend(result.test_cases)
            if result.llm_comments:
                llm_comments.append(result.llm_comments)
        logger.info(f"Generated {len(test_cases)} test cases in total.")
        return GeneratedTestCases(test_cases=test_cases, llm_comments="\n".join(llm_comments) or None)

    async def extract_acceptance_criteria(
        self, attachments_content: dict[str, BinaryContent], requirement_doc_content: str
    ) -> AcceptanceCriteriaList:
        user_message_parts: list[str | BinaryContent] = [f"Requirement document content:\n{requirement_doc_content}"]
        if attachments_content:
            for filename, binary_content in attachments_content.items():
                user_message_parts.append(f"Attachment: {filename}")
                user_message_parts.append(binary_content)

        logger.info("Starting AC extraction with %d attachments", len(attachments_content))
        result = await self.ac_extractor_agent.run(user_message_parts)
        extracted_acceptance_criteria: AcceptanceCriteriaList = result.output
        logger.info(f"Extracted {len(extracted_acceptance_criteria.items)} ACs")
        return extracted_acceptance_criteria

    @staticmethod
    def _upload_test_cases_into_test_management_system(
        test_cases: GeneratedTestCases, project_key: str, user_story_id: int
    ) -> str:
        """
        Uploads the provided test cases in the configured test management system.

        Args:
            test_cases: The list of test cases to be created.
            project_key: The key of the Jira project to which the Jira issue belongs.
            user_story_id: ID of the Jira user story (not its key), e.g. 120.

        Returns:
            A confirmation message with the keys (IDs) of the created test cases.
        """
        client = get_test_management_client()
        created_test_case_ids = client.create_test_cases(test_cases.test_cases, project_key, user_story_id)
        return f"Successfully created test cases with following keys (IDs): {', '.join(created_test_case_ids)}"


agent = TestCaseGenerationAgent()
app = agent.a2a_server

if __name__ == "__main__":
    agent.start_as_server()
