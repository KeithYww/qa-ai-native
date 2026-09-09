# SPDX-FileCopyrightText: 2025-2026 Taras Paruta (partarstu@gmail.com)
#
# SPDX-License-Identifier: AGPL-3.0-only

from a2a.types import AgentSkill
from pydantic_ai.settings import ThinkingLevel

import config
from agents.test_case_classification.prompt import TestCaseClassificationSystemPrompt
from common import utils
from common.agent_base import AgentBase
from common.models import ClassifiedTestCases, TestCaseKeys

logger = utils.get_logger("test_case_classification_agent")


class TestCaseClassificationAgent(AgentBase):
    __test__ = False

    def __init__(self):
        instruction_prompt = TestCaseClassificationSystemPrompt()
        super().__init__(
            agent_name=config.TestCaseClassificationAgentConfig.OWN_NAME,
            base_url=config.AGENT_BASE_URL,
            port=config.TestCaseClassificationAgentConfig.PORT,
            external_port=config.TestCaseClassificationAgentConfig.EXTERNAL_PORT,
            protocol=config.TestCaseClassificationAgentConfig.PROTOCOL,
            model_name=config.TestCaseClassificationAgentConfig.MODEL_NAME,
            fallback_model_name=config.TestCaseClassificationAgentConfig.FALLBACK_MODEL_NAME,
            output_type=ClassifiedTestCases,
            instructions=instruction_prompt.get_prompt(),
            deps_type=TestCaseKeys,
            mcp_servers=[],
            description="Agent which classifies test cases based on their content",
        )

    def get_thinking_level(self) -> ThinkingLevel:
        return config.TestCaseClassificationAgentConfig.THINKING_LEVEL

    def get_max_requests_per_task(self) -> int:
        return config.TestCaseClassificationAgentConfig.MAX_REQUESTS_PER_TASK

    def get_max_tokens(self) -> int | None:
        return config.TestCaseClassificationAgentConfig.MAX_TOKENS

    def get_skills(self) -> list[AgentSkill]:
        return [
            AgentSkill(
                id="classify-test-cases",
                name="Classify test cases",
                description="Classifies test cases based on their content",
                tags=["qa", "test-case-classification"],
            )
        ]

agent = TestCaseClassificationAgent()
app = agent.a2a_server

if __name__ == "__main__":
    agent.start_as_server()
