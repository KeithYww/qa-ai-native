# SPDX-FileCopyrightText: 2025-2026 Taras Paruta (partarstu@gmail.com)
#
# SPDX-License-Identifier: AGPL-3.0-only

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.types import Message

# Mock MCPServerSSE before importing the module
with patch("pydantic_ai.mcp.MCPServerSSE"):
    from agents.test_case_generation.main import TestCaseGenerationAgent, _generation_result_ctx

from common.agent_base import AgentBase
from common.models import (
    AcceptanceCriteriaItem,
    AcceptanceCriteriaList,
    GeneratedTestCases,
    TestCase,
    TestStep,
)
from common.services.test_management_base import TestManagementClientBase


@pytest.fixture
def mock_config():
    with patch("agents.test_case_generation.main.config") as mock_conf:
        mock_conf.TestCaseGenerationAgentConfig.OWN_NAME = "generation_agent"
        mock_conf.AGENT_BASE_URL = "http://localhost"
        mock_conf.TestCaseGenerationAgentConfig.PORT = 8002
        mock_conf.TestCaseGenerationAgentConfig.EXTERNAL_PORT = 8002
        mock_conf.TestCaseGenerationAgentConfig.PROTOCOL = "http"
        mock_conf.TestCaseGenerationAgentConfig.MODEL_NAME = "test"
        mock_conf.TestCaseGenerationAgentConfig.FALLBACK_MODEL_NAME = "test"
        mock_conf.TestCaseGenerationAgentConfig.THINKING_LEVEL = "MEDIUM"
        mock_conf.TestCaseGenerationAgentConfig.ORCHESTRATOR_THINKING_LEVEL = "MEDIUM"
        mock_conf.TestCaseGenerationAgentConfig.MAX_REQUESTS_PER_TASK = 10
        mock_conf.TestCaseGenerationAgentConfig.AC_BATCH_SIZE = 8
        mock_conf.TestCaseGenerationAgentConfig.TC_GENERATOR_MAX_TOKENS = 65536
        mock_conf.TestCaseGenerationAgentConfig.LLM_CONCURRENCY_LIMIT = 8
        mock_conf.JIRA_MCP_SERVER_URL = "http://jira-mcp"
        mock_conf.MCP_SERVER_TIMEOUT_SECONDS = 30
        yield mock_conf


@pytest.fixture
def agent(mock_config):
    with (
        patch("agents.test_case_generation.prompt.TestCaseGenerationSystemPrompt.get_prompt", return_value="Prompt"),
        patch("agents.test_case_generation.prompt.AcExtractionPrompt.get_prompt", return_value="AC Prompt"),
        patch("agents.test_case_generation.prompt.TcGenerationPrompt.get_prompt", return_value="TC Prompt"),
        patch("common.custom_llm_wrapper.Agent") as mock_agent_cls,
        patch("agents.test_case_generation.main.get_model", return_value="test"),
        patch("common.agent_base.get_model", return_value="test"),
    ):
        mock_agent_cls.side_effect = lambda *args, **kwargs: MagicMock()
        yield TestCaseGenerationAgent()


def test_agent_init(agent, mock_config):
    assert agent.agent_name == "generation_agent"
    assert agent.get_thinking_level() == "MEDIUM"
    assert agent.get_max_requests_per_task() == 10
    assert agent.ac_extractor_agent is not None
    assert agent.test_case_generator_agent is not None


@pytest.mark.asyncio
async def test_generate_test_cases_flow(agent):
    mock_ac_result = MagicMock()
    mock_ac_result.output = AcceptanceCriteriaList(
        items=[AcceptanceCriteriaItem(id="AC-1", text="Test criterion", attachment_info="")]
    )
    agent.ac_extractor_agent.run = AsyncMock(return_value=mock_ac_result)

    expected_test_cases = GeneratedTestCases(test_cases=[])
    mock_tc_result = MagicMock()
    mock_tc_result.output = expected_test_cases
    agent.test_case_generator_agent.run = AsyncMock(return_value=mock_tc_result)

    agent._fetch_attachments = MagicMock(return_value={})

    holder: list = []
    _generation_result_ctx.set(holder)
    result = await agent._generate_test_cases("Jira Content", ["/path/to/attachment.png"])

    assert isinstance(result, str)
    assert "0" in result
    assert holder == [expected_test_cases]
    agent._fetch_attachments.assert_called_once_with(["/path/to/attachment.png"])
    agent.ac_extractor_agent.run.assert_called_once()
    # 1 AC with batch_size=8 → exactly 1 batch call
    agent.test_case_generator_agent.run.assert_called_once()


@pytest.mark.asyncio
async def test_generate_test_cases_from_acs_batches(agent):
    """16 ACs with batch_size=8 must produce exactly 2 generator calls and merge results."""
    ac_items = [
        AcceptanceCriteriaItem(id=f"AC-{i}", text=f"Criterion {i}", attachment_info="")
        for i in range(16)
    ]
    ac_list = AcceptanceCriteriaList(items=ac_items)

    tc_a = TestCase(
        key=None, name="TC-A", summary="s", comment="", preconditions=None,
        parent_issue_key=None, steps=[TestStep(action="a", expected_results="r", test_data=[])], labels=[],
    )
    tc_b = TestCase(
        key=None, name="TC-B", summary="s", comment="", preconditions=None,
        parent_issue_key=None, steps=[TestStep(action="b", expected_results="r", test_data=[])], labels=[],
    )
    mock_r1, mock_r2 = MagicMock(), MagicMock()
    mock_r1.output = GeneratedTestCases(test_cases=[tc_a])
    mock_r2.output = GeneratedTestCases(test_cases=[tc_b])
    agent.test_case_generator_agent.run = AsyncMock(side_effect=[mock_r1, mock_r2])

    result = await agent.generate_test_cases_from_acs(ac_list, "doc content")

    assert agent.test_case_generator_agent.run.call_count == 2
    assert result.test_cases == [tc_a, tc_b]


@pytest.mark.asyncio
async def test_run_returns_full_result_via_context_var(agent):
    tc = TestCase(
        key=None, name="TC-1", summary="s", comment="", preconditions=None,
        parent_issue_key=None,
        steps=[TestStep(action="a", expected_results="r", test_data=[])],
        labels=[],
    )
    full_result = GeneratedTestCases(test_cases=[tc] * 10, llm_comments=None)
    mock_message = MagicMock(spec=Message)
    mock_message.context_id = "ctx-1"
    mock_message.task_id = "task-1"

    async def fake_super_run(self_arg, msg):
        _generation_result_ctx.get().append(full_result)
        return MagicMock(spec=Message)

    with patch.object(AgentBase, "run", new=fake_super_run):
        result = await agent.run(mock_message)

    body = GeneratedTestCases.model_validate_json(result.parts[0].text)
    assert len(body.test_cases) == 10


@pytest.mark.asyncio
async def test_run_falls_back_when_tool_not_called(agent):
    mock_message = MagicMock(spec=Message)
    fallback_message = MagicMock(spec=Message)

    async def fake_super_run(self_arg, msg):
        return fallback_message

    with patch.object(AgentBase, "run", new=fake_super_run):
        result = await agent.run(mock_message)

    assert result is fallback_message


@patch("agents.test_case_generation.main.get_test_management_client")
def test_upload_test_cases(mock_get_client, agent):
    mock_client = MagicMock(spec=TestManagementClientBase)
    mock_get_client.return_value = mock_client
    mock_client.create_test_cases.return_value = ["TC-1", "TC-2"]

    tcs = GeneratedTestCases(test_cases=[])
    result = agent._upload_test_cases_into_test_management_system(tcs, "PROJ", 123)

    mock_client.create_test_cases.assert_called_once()
    assert "TC-1, TC-2" in result
