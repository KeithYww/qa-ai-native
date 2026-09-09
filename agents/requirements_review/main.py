# SPDX-FileCopyrightText: 2025-2026 Taras Paruta (partarstu@gmail.com)
#
# SPDX-License-Identifier: AGPL-3.0-only

from a2a.types import AgentSkill
from pydantic_ai.mcp import MCPServerSSE
from pydantic_ai.settings import ThinkingLevel

import config
from agents.requirements_review.prompt import RequirementsReviewContentPrompt, RequirementsReviewSystemPrompt
from common import utils
from common.agent_base import AgentBase
from common.custom_llm_wrapper import CustomLlmWrapper
from common.llm_provider import get_model
from common.models import RequirementsReviewFeedback
from common.services.meego_client import MeegoClient

logger = utils.get_logger("reviewer_agent")
feishu_mcp_server = MCPServerSSE(url=config.FEISHU_MCP_SERVER_URL, timeout=config.MCP_SERVER_TIMEOUT_SECONDS)


class RequirementsReviewAgent(AgentBase):
    def __init__(self):
        # Create a sub-agent for reviewing requirement document content
        self.review_agent = CustomLlmWrapper.create_agent(
            model_name=get_model(
                config.RequirementsReviewAgentConfig.MODEL_NAME,
                config.RequirementsReviewAgentConfig.FALLBACK_MODEL_NAME,
            ),
            output_type=RequirementsReviewFeedback,
            system_prompt=RequirementsReviewContentPrompt().get_prompt(),
            name="review_requirement_doc",
            thinking_level=config.RequirementsReviewAgentConfig.THINKING_LEVEL,
        )

        instruction_prompt = RequirementsReviewSystemPrompt()
        super().__init__(
            agent_name=config.RequirementsReviewAgentConfig.OWN_NAME,
            base_url=config.AGENT_BASE_URL,
            port=config.RequirementsReviewAgentConfig.PORT,
            external_port=config.RequirementsReviewAgentConfig.EXTERNAL_PORT,
            protocol=config.RequirementsReviewAgentConfig.PROTOCOL,
            model_name=config.RequirementsReviewAgentConfig.MODEL_NAME,
            fallback_model_name=config.RequirementsReviewAgentConfig.FALLBACK_MODEL_NAME,
            output_type=RequirementsReviewFeedback,
            instructions=instruction_prompt.get_prompt(),
            mcp_servers=[feishu_mcp_server],
            description="Agent which does the review of requirements, including Feishu Project story work items",
            tools=[self._review_requirement_doc, self.add_requirement_review_comment],
        )

    def get_thinking_level(self) -> ThinkingLevel:
        return config.RequirementsReviewAgentConfig.THINKING_LEVEL

    def get_max_requests_per_task(self) -> int:
        return config.RequirementsReviewAgentConfig.MAX_REQUESTS_PER_TASK

    def get_skills(self) -> list[AgentSkill]:
        return [
            AgentSkill(
                id="review-requirements",
                name="Review requirements",
                description="Reviews requirements, including Feishu Project story work items, and flags gaps or duplicates.",
                tags=["qa", "requirements-review"],
            )
        ]

    async def _review_requirement_doc(self, requirement_doc_content: str) -> RequirementsReviewFeedback:
        """
        Reviews a requirement document's content.

        Args:
            requirement_doc_content: The complete content of the requirement document.

        Returns:
            Requirements review feedback with improvement suggestions.
        """
        logger.info("Starting requirements review")
        result = await self.review_agent.run(f"Requirement document content:\n```{requirement_doc_content}```")
        feedback: RequirementsReviewFeedback = result.output
        logger.info("Generated improvement suggestions as a feedback")
        return feedback

    @staticmethod
    def add_requirement_review_comment(work_item_id: str, project_key: str, comment: str) -> str:
        """
        Adds a comment (e.g. a review feedback) to a Feishu Project work item. Always use exactly this tool
        if you need to add a comment to the requirement document's work item.

        Args:
            work_item_id: The ID of the Feishu Project work item (e.g. a story) to comment on.
            project_key: The Feishu Project space key the work item belongs to.
            comment: The text of the comment to add.

        Returns:
            A success message.
        """
        MeegoClient(project_key).add_work_item_comment(project_key, "story", work_item_id, comment)
        logger.info(f"Added requirement review comment to work item {work_item_id}.")
        return f"Successfully added comment to work item {work_item_id}."


agent = RequirementsReviewAgent()
app = agent.a2a_server

if __name__ == "__main__":
    agent.start_as_server()
