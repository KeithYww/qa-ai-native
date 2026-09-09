# SPDX-FileCopyrightText: 2025-2026 Taras Paruta (partarstu@gmail.com)
#
# SPDX-License-Identifier: AGPL-3.0-only

from unittest.mock import patch

import pytest

from agents.test_case_classification.main import TestCaseClassificationAgent


@pytest.fixture
def mock_config():
    with patch("agents.test_case_classification.main.config") as mock_conf:
        # Set config values required by AgentBase init
        mock_conf.TestCaseClassificationAgentConfig.OWN_NAME = "classification_agent"
        mock_conf.AGENT_BASE_URL = "http://localhost"
        mock_conf.TestCaseClassificationAgentConfig.PORT = 8001
        mock_conf.TestCaseClassificationAgentConfig.EXTERNAL_PORT = 8001
        mock_conf.TestCaseClassificationAgentConfig.PROTOCOL = "http"
        mock_conf.TestCaseClassificationAgentConfig.MODEL_NAME = "test"
        mock_conf.TestCaseClassificationAgentConfig.FALLBACK_MODEL_NAME = "test"
        mock_conf.TestCaseClassificationAgentConfig.THINKING_LEVEL = "LOW"
        mock_conf.TestCaseClassificationAgentConfig.MAX_REQUESTS_PER_TASK = 5
        yield mock_conf


@pytest.fixture
def agent(mock_config):
    # Patch PromptBase.get_prompt to avoid file reading issues, and get_model to avoid
    # constructing a real Anthropic provider/client during tests.
    with (
        patch(
            "agents.test_case_classification.prompt.TestCaseClassificationSystemPrompt.get_prompt",
            return_value="Prompt",
        ),
        patch("common.agent_base.get_model", return_value="test"),
    ):
        return TestCaseClassificationAgent()


def test_agent_init(agent, mock_config):
    assert agent.agent_name == "classification_agent"
    assert agent.get_thinking_level() == "LOW"
    assert agent.get_max_requests_per_task() == 5
