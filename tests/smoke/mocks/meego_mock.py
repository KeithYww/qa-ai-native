# SPDX-FileCopyrightText: 2025-2026 Taras Paruta (partarstu@gmail.com)
#
# SPDX-License-Identifier: AGPL-3.0-only

"""Stateful recording mock for the Feishu Project (Meego) Open API surface.

Serves the endpoints ``MeegoClient`` exercises: plugin-token auth, work-item
create, comment create (both the ID-in-path and ID-in-body contracts), status
transition, and label patch. Records every creation/mutation in memory and
exposes the aggregate state at ``GET /__recorded`` for the smoke assertions.
"""

from fastapi import FastAPI, Request

app = FastAPI()

# Keyed by work_item_id; each value holds the merged state for that work item.
_work_items: dict[str, dict] = {}
_counter = 0


def _get_or_create(work_item_id: str) -> dict:
    if work_item_id not in _work_items:
        _work_items[work_item_id] = {
            "work_item_id": work_item_id,
            "name": "",
            "steps": "",
            "expected": "",
            "labels": [],
            "comments": [],
            "transitions": [],
        }
    return _work_items[work_item_id]


@app.post("/open_api/authing/plugin_token")
async def get_plugin_token() -> dict:
    return {"code": 0, "data": {"token": "smoke-plugin-token", "expire_time": 7200}}


@app.post("/open_api/{project_key}/work_item/create")
async def create_work_item(project_key: str, request: Request) -> dict:
    global _counter
    payload = await request.json()
    _counter += 1
    work_item_id = f"MEEGO-{_counter}"
    entry = _get_or_create(work_item_id)
    entry["project_key"] = project_key
    for field in payload.get("fields", []):
        key = field.get("field_key", "")
        value = field.get("field_value", "")
        if key == "name":
            entry["name"] = value
        elif key == "field_023f96":
            entry["steps"] = value
        elif key == "field_2c7371":
            entry["expected"] = value
        elif key == "description":
            entry["description"] = value
    return {"code": 0, "data": {"work_item_id": work_item_id}}


@app.post("/open_api/{project_key}/work_item/{work_item_id}/comment/create")
async def add_comment(project_key: str, work_item_id: str, request: Request) -> dict:
    payload = await request.json()
    entry = _get_or_create(work_item_id)
    entry["comments"].append(payload.get("content", ""))
    return {"code": 0}


@app.post("/open_api/{project_key}/work_item/comment/create")
async def add_comment_by_body(project_key: str, request: Request) -> dict:
    """Distinct contract from add_comment above: the work item ID travels in the body,
    not the path (see MeegoClient.add_work_item_comment)."""
    payload = await request.json()
    work_item_id = str(payload.get("work_item_id", ""))
    entry = _get_or_create(work_item_id)
    entry["project_key"] = project_key
    entry["comments"].append(payload.get("content", ""))
    return {"code": 0}


@app.post("/open_api/{project_key}/work_item/{work_item_id}/transition_state")
async def transition_state(project_key: str, work_item_id: str, request: Request) -> dict:
    payload = await request.json()
    entry = _get_or_create(work_item_id)
    entry["transitions"].append(payload.get("transition_status_id", ""))
    return {"code": 0}


@app.patch("/open_api/{project_key}/work_item/{work_item_id}")
async def update_work_item(project_key: str, work_item_id: str, request: Request) -> dict:
    payload = await request.json()
    entry = _get_or_create(work_item_id)
    for field in payload.get("fields", []):
        if field.get("field_key") == "field_65e1cc":
            entry["labels"] = field.get("field_value", [])
    return {"code": 0}


@app.get("/__recorded")
async def recorded() -> dict:
    return {"test_cases": list(_work_items.values())}
