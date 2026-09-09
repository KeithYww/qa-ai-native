# SPDX-FileCopyrightText: 2025-2026 Taras Paruta (partarstu@gmail.com)
#
# SPDX-License-Identifier: AGPL-3.0-only

from unittest.mock import MagicMock, patch

import pytest

import config
from agents.requirements_review.main import RequirementsReviewAgent


@pytest.fixture
def mock_config(monkeypatch):
    monkeypatch.setattr(config.RequirementsReviewAgentConfig, "OWN_NAME", "Test Agent")
    monkeypatch.setattr(config.RequirementsReviewAgentConfig, "PORT", 8001)
    monkeypatch.setattr(config.RequirementsReviewAgentConfig, "EXTERNAL_PORT", 8001)
    monkeypatch.setattr(config.RequirementsReviewAgentConfig, "PROTOCOL", "http")
    monkeypatch.setattr(config.RequirementsReviewAgentConfig, "MODEL_NAME", "test")
    monkeypatch.setattr(config.RequirementsReviewAgentConfig, "FALLBACK_MODEL_NAME", "test")
    monkeypatch.setattr(config.RequirementsReviewAgentConfig, "THINKING_LEVEL", "LOW")
    monkeypatch.setattr(config.RequirementsReviewAgentConfig, "MAX_REQUESTS_PER_TASK", 5)
    monkeypatch.setattr(config, "AGENT_BASE_URL", "http://localhost")
    monkeypatch.setattr(config, "FEISHU_MCP_SERVER_URL", "http://feishu")
    monkeypatch.setattr(config, "MCP_SERVER_TIMEOUT_SECONDS", 30)


@patch("agents.requirements_review.main.RequirementsReviewSystemPrompt")
@patch("agents.requirements_review.main.AgentBase.__init__")
@patch("agents.requirements_review.main.get_model", return_value="test")
def test_requirements_review_agent_init(mock_get_model, mock_super_init, mock_prompt_cls, mock_config):
    mock_prompt_instance = MagicMock()
    mock_prompt_instance.get_prompt.return_value = "system prompt"
    mock_prompt_cls.return_value = mock_prompt_instance

    agent = RequirementsReviewAgent()

    mock_super_init.assert_called_once()
    _, kwargs = mock_super_init.call_args
    assert kwargs["agent_name"] == "Test Agent"
    assert kwargs["instructions"] == "system prompt"

    assert agent.get_thinking_level() == "LOW"
    assert agent.get_max_requests_per_task() == 5


def test_add_requirement_review_comment_posts_via_meego_client():
    mock_client = MagicMock()
    with patch("agents.requirements_review.main.MeegoClient", return_value=mock_client) as mock_cls:
        result = RequirementsReviewAgent.add_requirement_review_comment("WI-1", "PROJ", "feedback text")

    mock_cls.assert_called_once_with("PROJ")
    mock_client.add_work_item_comment.assert_called_once_with("PROJ", "story", "WI-1", "feedback text")
    assert "WI-1" in result
