"""
Starts all services in a single process / single event loop.
Each service listens on its own port; inter-service traffic uses localhost.
"""

import asyncio
import logging

import uvicorn

from agents.test_case_classification.main import app as classification_app
from agents.test_case_generation.main import app as generation_app
from agents.test_case_review.main import app as review_app
from orchestrator.main import orchestrator_app
from scripts.feishu_mcp_server import app as feishu_mcp_app

logger = logging.getLogger(__name__)

_SERVICES: list[tuple[str, object, int]] = [
    ("orchestrator", orchestrator_app, 8000),
    ("tc-generation", generation_app, 8002),
    ("tc-classification", classification_app, 8003),
    ("tc-review", review_app, 8004),
    ("feishu-mcp", feishu_mcp_app, 9010),
]


async def _main() -> None:
    servers = [
        uvicorn.Server(
            uvicorn.Config(app, host="0.0.0.0", port=port, log_config=None)
        )
        for _, app, port in _SERVICES
    ]
    for (name, _, port), server in zip(_SERVICES, servers):
        logger.info("Starting %s on port %d", name, port)
    await asyncio.gather(*[s.serve() for s in servers])


if __name__ == "__main__":
    asyncio.run(_main())
