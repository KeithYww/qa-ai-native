# SPDX-FileCopyrightText: 2025-2026 Taras Paruta (partarstu@gmail.com)
#
# SPDX-License-Identifier: AGPL-3.0-only

"""Mock for the Feishu Open API HTTP endpoints used by feishu_get_doc_content.

Handles token auth, raw document content fetch, and wiki node resolution
so the orchestrator's pre-fetch path works without real Feishu credentials.
"""

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.requests import Request
from starlette.routing import Route

SEEDED_DOC_CONTENT = (
    "Title: Smoke password-reset PRD\n\n"
    "As a registered user I want to reset my password via an email link "
    "so that I can regain access when I forget it.\n\n"
    "Acceptance Criteria:\n"
    "1. A 'Forgot password' link on the login page opens a form for an email address.\n"
    "2. Submitting a registered email queues a password-reset link expiring after 60 minutes.\n"
    "3. Submitting an unregistered email shows the same confirmation message.\n"
    "4. Following a valid link lets the user set a new password meeting the complexity policy.\n"
    "5. An expired or already-used link shows an error and offers to request a new one.\n"
)

_recorded: dict[str, list] = {"fetched_docs": []}

app = FastAPI(title="feishu-api-mock")


@app.post("/auth/v3/tenant_access_token/internal")
async def get_tenant_access_token() -> JSONResponse:
    return JSONResponse({"tenant_access_token": "mock_token", "expire": 7200, "code": 0, "msg": "ok"})


@app.get("/docx/v1/documents/{doc_id}/raw_content")
async def get_doc_raw_content(doc_id: str) -> JSONResponse:
    _recorded["fetched_docs"].append(doc_id)
    return JSONResponse({"code": 0, "msg": "ok", "data": {"content": SEEDED_DOC_CONTENT}})


@app.get("/wiki/v2/spaces/get_node")
async def get_wiki_node(token: str) -> JSONResponse:
    return JSONResponse(
        {"code": 0, "msg": "ok", "data": {"node": {"obj_token": "smoke_doc_token", "obj_type": "docx"}}}
    )


@app.get("/__recorded")
async def recorded() -> JSONResponse:
    return JSONResponse(_recorded)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8092)
