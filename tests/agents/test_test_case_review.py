# SPDX-FileCopyrightText: 2025-2026 Taras Paruta (partarstu@gmail.com)
#
# SPDX-License-Identifier: AGPL-3.0-only

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.helpers import new_text_message
from a2a.types import Role

from agents.test_case_review.main import TestCaseReviewAgent, _parse_test_cases_from_text

from common.models import TestCase, TestCaseReviewFeedback, TestCaseReviewFeedbacks, TestStep


@pytest.fixture
def mock_config():
    with patch("agents.test_case_review.main.config") as mock_conf:
        mock_conf.TestCaseReviewAgentConfig.OWN_NAME = "review_agent"
        mock_conf.AGENT_BASE_URL = "http://localhost"
        mock_conf.TestCaseReviewAgentConfig.PORT = 8003
        mock_conf.TestCaseReviewAgentConfig.EXTERNAL_PORT = 8003
        mock_conf.TestCaseReviewAgentConfig.PROTOCOL = "http"
        mock_conf.TestCaseReviewAgentConfig.MODEL_NAME = "test"
        mock_conf.TestCaseReviewAgentConfig.FALLBACK_MODEL_NAME = "test"
        mock_conf.TestCaseReviewAgentConfig.THINKING_LEVEL = "LOW"
        mock_conf.TestCaseReviewAgentConfig.MAX_REQUESTS_PER_TASK = 8
        yield mock_conf


@pytest.fixture
def agent(mock_config):
    # Patch PromptBase.get_prompt to avoid file reading issues, and get_model to avoid
    # constructing a real Anthropic provider/client during tests.
    with (
        patch("agents.test_case_review.prompt.TestCaseReviewSystemPrompt.get_prompt", return_value="Prompt"),
        patch("agents.test_case_review.main.get_model", return_value="test"),
        patch("common.agent_base.get_model", return_value="test"),
    ):
        return TestCaseReviewAgent()


def test_agent_init(agent, mock_config):
    assert agent.agent_name == "review_agent"
    assert agent.get_thinking_level() == "LOW"
    assert agent.get_max_requests_per_task() == 8

@pytest.mark.asyncio
async def test_run_returns_result_set_by_tool_in_separate_task(agent):
    """Reproduces pydantic-ai's real tool-execution model, where each tool call runs in its
    own asyncio.Task (a copy of the caller's contextvars.Context). A ContextVar mutation made
    inside that task must reach run() via the shared holder object, not via ContextVar.set()."""
    expected_feedbacks = TestCaseReviewFeedbacks(
        review_feedbacks=[TestCaseReviewFeedback(test_case_id="TC-1", review_feedback=["Looks good"])]
    )

    async def fake_super_run(received_message):
        task = asyncio.create_task(
            agent._review_test_cases_with_attachments(ctx=MagicMock())
        )
        await task
        return new_text_message(text="fallback", role=Role.ROLE_AGENT)

    with (
        patch("agents.test_case_review.main.AgentBase.run", side_effect=fake_super_run),
        patch("agents.test_case_review.main._parse_test_cases_from_text", return_value=["TC-1"]),
        patch.object(agent, "_fetch_attachments", return_value={}),
        patch.object(agent, "_review_batch", new=AsyncMock(return_value=expected_feedbacks)),
    ):
        received_message = new_text_message(text="review these test cases", role=Role.ROLE_USER)
        result = await agent.run(received_message)

    assert TestCaseReviewFeedbacks.model_validate_json(result.parts[0].text) == expected_feedbacks


def _make_tc_json(name: str = "TC-1") -> str:
    tc = TestCase(
        key=None,
        name=name,
        summary="s",
        comment="",
        preconditions=None,
        parent_issue_key=None,
        steps=[TestStep(action="a", expected_results="r", test_data=[])],
        labels=[],
    )
    import json
    return json.dumps({"test_cases": [tc.model_dump()]})


def test_parse_test_cases_plain():
    """Finds test_cases when the JSON is the only content."""
    result = _parse_test_cases_from_text(_make_tc_json())
    assert len(result) == 1


def test_parse_test_cases_preceded_by_json_without_key():
    """Skips a preceding JSON object that lacks 'test_cases' and finds the real one."""
    noise = '{"foo": "bar", "nested": {"x": 1}}'
    text = f"Some doc content with JSON: {noise}\n\nTest cases:\n{_make_tc_json()}"
    result = _parse_test_cases_from_text(text)
    assert len(result) == 1


def test_parse_test_cases_doc_content_with_braces():
    """Handles requirement doc content that contains multiple { } blocks before the test cases."""
    doc = "Function call: getUser({id: 1}) and another obj: {key: value}"
    text = f"Requirement document content:\n{doc}\n\nTest cases:\n{_make_tc_json('TC-2')}"
    result = _parse_test_cases_from_text(text)
    assert len(result) == 1
    assert result[0].name == "TC-2"


def test_parse_test_cases_returns_empty_when_no_json():
    assert _parse_test_cases_from_text("no json here at all") == []


def test_parse_test_cases_returns_empty_when_no_test_cases_key():
    assert _parse_test_cases_from_text('{"foo": "bar"}') == []
