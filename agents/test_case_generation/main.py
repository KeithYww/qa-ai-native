# SPDX-FileCopyrightText: 2025-2026 Taras Paruta (partarstu@gmail.com)
#
# SPDX-License-Identifier: AGPL-3.0-only

import asyncio
import re
from dataclasses import dataclass
from itertools import batched
from typing import Literal

from a2a.helpers import new_text_message
from a2a.types import AgentSkill, Message
from pydantic_ai import RunContext
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
)
from common.services.test_management_system_client_provider import get_test_management_client

logger = utils.get_logger("test_case_generation_agent")
feishu_mcp_server = MCPServerSSE(url=config.FEISHU_MCP_SERVER_URL, timeout=config.MCP_SERVER_TIMEOUT_SECONDS)

_AC_SIGNALS = ("验收标准", "Acceptance Criteria", "acceptance criteria")
_SECTION_NUMBER_RE = re.compile(r"(?<!\d)\d+\.\d+(?:\.\d+)?(?!\d)")
_HTML_HEADING_RE = re.compile(r"<h[23][^>]*>(.*?)</h[23]>", re.IGNORECASE | re.DOTALL)
_NUMBERED_TITLE_RE = re.compile(r"^(\d+(?:\.\d+)+)\s+(.+)$", re.MULTILINE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(slots=True)
class _Section:
    """Internal representation of a PRD section before it becomes a chunk."""

    section_id: str
    title: str
    content: str

    @property
    def char_count(self) -> int:
        return len(self.content)


def _classify_prd(content: str) -> Literal["ac_list", "functional", "narrative"]:
    """Classify a PRD by type to select the appropriate chunking strategy.

    Returns:
        "ac_list"    - PRD contains an explicit acceptance-criteria list.
        "functional" - PRD is structured with numbered sections (no explicit AC list).
        "narrative"  - No detectable structure; fall back to LLM-based AC derivation.
    """
    if any(signal in content for signal in _AC_SIGNALS):
        return "ac_list"
    if len(_SECTION_NUMBER_RE.findall(content)) >= config.PrdClassifierConfig.FUNCTIONAL_SECTION_THRESHOLD:
        return "functional"
    return "narrative"


def _strip_html(text: str) -> str:
    return _HTML_TAG_RE.sub(" ", text).strip()


def _parse_sections_from_html(html: str) -> list[_Section]:
    """Extract sections from lark-cli HTML output using heading tags."""
    sections: list[_Section] = []
    parts = _HTML_HEADING_RE.split(html)
    # parts layout: [text_before_h1, heading_text, body_after_heading, ...]
    i = 1
    while i < len(parts) - 1:
        raw_title = _strip_html(parts[i])
        body = _strip_html(parts[i + 1])
        # Derive section id from leading number pattern in the title
        m = re.match(r"(\d+(?:\.\d+)*)", raw_title)
        section_id = m.group(1) if m else f"sec-{len(sections) + 1}"
        sections.append(_Section(section_id=section_id, title=raw_title, content=body))
        i += 2
    return sections


def _parse_sections_from_text(text: str) -> list[_Section]:
    """Fallback: extract numbered sections from plain text."""
    matches = list(_NUMBERED_TITLE_RE.finditer(text))
    sections: list[_Section] = []
    for idx, match in enumerate(matches):
        section_id = match.group(1)
        title = match.group(2).strip()
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        sections.append(_Section(section_id=section_id, title=title, content=text[start:end].strip()))
    return sections


def _merge_short_sections(sections: list[_Section], min_chars: int) -> list[_Section]:
    """Merge sections below min_chars into the previous section."""
    if not sections:
        return sections
    merged: list[_Section] = [sections[0]]
    for sec in sections[1:]:
        if sec.char_count < min_chars:
            prev = merged[-1]
            merged[-1] = _Section(
                section_id=prev.section_id,
                title=prev.title,
                content=f"{prev.content}\n\n### {sec.title}\n{sec.content}",
            )
        else:
            merged.append(sec)
    return merged


def _split_long_sections(sections: list[_Section], max_chars: int) -> list[_Section]:
    """Split sections above max_chars on sub-heading boundaries."""
    result: list[_Section] = []
    for sec in sections:
        if sec.char_count <= max_chars:
            result.append(sec)
            continue
        # Split on sub-numbered lines within the body
        sub_matches = list(_NUMBERED_TITLE_RE.finditer(sec.content))
        if not sub_matches:
            result.append(sec)
            continue
        # First chunk: content before first sub-heading
        preamble = sec.content[: sub_matches[0].start()].strip()
        if preamble:
            result.append(_Section(section_id=sec.section_id, title=sec.title, content=preamble))
        for i, m in enumerate(sub_matches):
            sub_id = m.group(1)
            sub_title = m.group(2).strip()
            sub_start = m.end()
            sub_end = sub_matches[i + 1].start() if i + 1 < len(sub_matches) else len(sec.content)
            result.append(
                _Section(
                    section_id=sub_id,
                    title=sub_title,
                    content=sec.content[sub_start:sub_end].strip(),
                )
            )
    return result


def _chunk_by_sections(content: str) -> AcceptanceCriteriaList:
    """Convert a functional PRD into requirement chunks keyed by section number."""
    sections = _parse_sections_from_html(content)
    if not sections:
        logger.info("HTML section parsing yielded no sections; falling back to text parsing.")
        sections = _parse_sections_from_text(content)

    min_chars = config.PrdClassifierConfig.SECTION_MIN_CHARS
    max_chars = config.PrdClassifierConfig.SECTION_MAX_CHARS
    sections = _merge_short_sections(sections, min_chars)
    sections = _split_long_sections(sections, max_chars)

    logger.info(
        f"Section chunker produced {len(sections)} chunks "
        f"(min_chars={min_chars}, max_chars={max_chars})."
    )
    return AcceptanceCriteriaList(
        items=[
            AcceptanceCriteriaItem(
                id=s.section_id,
                text=f"{s.title}\n\n{s.content}",
                attachment_info="",
                source_type="section",
            )
            for s in sections
            if s.content.strip()
        ]
    )


@dataclass(slots=True)
class _GenerationRunState:
    """Per-request state container for TestCaseGenerationAgent, threaded via pydantic-ai deps."""

    result: GeneratedTestCases | None = None


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
            deps_type=_GenerationRunState,
            description="Agent which generates test cases based on requirement documents.",
            tools=[self._generate_test_cases],
        )
        # ac_extractor_agent holds its own MCP toolset (feishu_mcp_server), separate from
        # the outer agent's (empty) mcp_servers, so it must be registered here too to get
        # the lifespan-held connection protection from AgentBase._lifespan.
        self._mcp_managed_agents.append(self.ac_extractor_agent)

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
        self, ctx: RunContext[_GenerationRunState], requirement_doc_content: str, attachment_paths: list[str]
    ) -> str:
        """
        Generates test cases based on the requirement document content and attachments.

        Classifies the PRD type first, then routes to the appropriate chunking
        strategy before TC generation.  Stores the result in ctx.deps.result.

        Args:
            ctx: Run context providing access to per-request state.
            requirement_doc_content: The whole content of the requirement document.
            attachment_paths: List of file paths to the downloaded attachments.

        Returns:
            Summary string; full result is stored in ctx.deps for run() to retrieve.
        """
        doc_type = _classify_prd(requirement_doc_content)
        logger.info(f"PRD classified as: '{doc_type}'")

        match doc_type:
            case "functional":
                chunks = _chunk_by_sections(requirement_doc_content)
            case _:
                attachments_content = self._fetch_attachments(attachment_paths)
                chunks = await self.extract_acceptance_criteria(attachments_content, requirement_doc_content)

        generated_test_cases = await self.generate_test_cases_from_acs(chunks, requirement_doc_content)
        ctx.deps.result = generated_test_cases
        return f"Successfully generated {len(generated_test_cases.test_cases)} test cases."

    async def run(self, received_message: Message) -> Message:
        state = _GenerationRunState()
        result_message = await super().run(received_message, deps=state)
        if state.result is None:
            return result_message
        context_id = getattr(received_message, "context_id", None)
        task_id = getattr(received_message, "task_id", None)
        return new_text_message(
            text=state.result.model_dump_json(),
            context_id=context_id,
            task_id=task_id,
        )

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
