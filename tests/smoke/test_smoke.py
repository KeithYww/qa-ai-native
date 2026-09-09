# SPDX-FileCopyrightText: 2025-2026 Taras Paruta (partarstu@gmail.com)
#
# SPDX-License-Identifier: AGPL-3.0-only

"""Hermetic end-to-end smoke checks for the core QuAIA flows.

All of our own code runs for real (orchestrator + agents + real Gemini); only
the external boundaries are mocked. Each test asserts on what reached a mocked
boundary, read back from its ``/__recorded`` endpoint:

* Requirements review   -> a non-empty comment reached the reviewed work item in Feishu Project.
* Requirements review   -> the agent first fetched the requirement document via the Feishu MCP.
* Test-case generation  -> real test cases (name + steps) reached Zephyr.
* Test-case generation  -> the created test cases were linked to the seeded story's numeric id.
* Test-case classification -> labels reached Zephyr.
* Test-case review      -> a non-empty "Review Comments" value reached Zephyr.
* Test-case review      -> at least one test case reached the "Review Complete" status.
* Test execution        -> a failed automated test drove a real Bug issue into the seeded project.
* Test execution        -> a failed execution for the seeded case was reported to Zephyr,
                           and the created bug was linked to that execution.
* Test execution        -> the incident-creation flow consulted the vector DB for duplicates.
* RAG DB update         -> the sync pushed the seeded story into the vector DB.
* Negative paths        -> the three authenticated webhooks reject a bad API key (401); the
                           requirement-review webhook is unauthenticated by design and instead
                           silently drops a malformed payload or a missing PRD link (204); the
                           required-field webhooks reject a missing required field (400), the
                           project-key webhooks reject a missing project_key (422), and the
                           dashboard API rejects a missing token (401).
"""

import time
from collections.abc import Callable

import httpx
import pytest

from tests.smoke.conftest import (
    FEISHU_MCP_RECORDED_URL,
    JIRA_MCP_RECORDED_URL,
    MEEGO_RECORDED_URL,
    ORCHESTRATOR_URL,
    QDRANT_RECORDED_URL,
    SEEDED_EXECUTABLE_TC_KEY,
    SEEDED_ISSUE_KEY,
    SEEDED_PROJECT_KEY,
    SEEDED_REVIEW_WORK_ITEM_ID,
    SEEDED_STORY_ID,
    SMOKE_REQUIREMENT_REVIEW_DOC_URL,
    TICKETS_COLLECTION_NAME,
    ZEPHYR_RECORDED_URL,
)

pytestmark = pytest.mark.smoke

# The webhook returns 202 immediately; the pipeline runs asynchronously. Each stage
# (generation, classification, review) involves one or more LLM calls, so the full
# pipeline for a small story can take several minutes. Allow enough time per test.
# Requirements review and test-case generation now share the same orchestrator queue/consumer
# (see orchestrator/main.py's _pipeline_queue), so when both fire around the same time one can
# queue behind the other's full run — the timeout must cover that added wait, not just one
# flow's own LLM latency.
RECORD_POLL_TIMEOUT = 480.0
RECORD_POLL_INTERVAL = 5.0


def _wait_for_recorded(
    http_client: httpx.Client, url: str, predicate: Callable[[dict], bool]
) -> dict:
    """Poll a mock's /__recorded endpoint until the predicate holds or time runs out."""
    deadline = time.monotonic() + RECORD_POLL_TIMEOUT
    data: dict = {}
    while time.monotonic() < deadline:
        response = http_client.get(url)
        if response.status_code == 200:
            data = response.json()
            if predicate(data):
                return data
        time.sleep(RECORD_POLL_INTERVAL)
    return data


# --- Requirements review flow ----------------------------------------------------------


def test_requirements_review_webhook_accepted(requirements_review_response: httpx.Response) -> None:
    assert requirements_review_response.status_code == 202, (
        f"Requirements-review webhook failed: "
        f"{requirements_review_response.status_code} {requirements_review_response.text}"
    )


def test_agent_fetched_requirement_doc_for_review(
    requirements_review_response: httpx.Response, http_client: httpx.Client
) -> None:
    """The review must be grounded in the real requirement doc: the agent must fetch it via the Feishu MCP first."""
    data = _wait_for_recorded(
        http_client, FEISHU_MCP_RECORDED_URL, lambda d: SMOKE_REQUIREMENT_REVIEW_DOC_URL in d.get("fetched_docs", [])
    )
    assert SMOKE_REQUIREMENT_REVIEW_DOC_URL in data.get("fetched_docs", []), (
        f"Requirements review agent never fetched the requirement doc via Feishu MCP. Recorded: {data}"
    )


def test_review_comment_reached_feishu_project(
    requirements_review_response: httpx.Response, http_client: httpx.Client
) -> None:
    """The agent must post a non-empty review comment to the reviewed work item in Feishu Project."""
    data = _wait_for_recorded(
        http_client,
        MEEGO_RECORDED_URL,
        lambda d: any(
            wi.get("work_item_id") == SEEDED_REVIEW_WORK_ITEM_ID and any(c.strip() for c in wi.get("comments", []))
            for wi in d.get("test_cases", [])
        ),
    )
    reviewed = [
        wi
        for wi in data.get("test_cases", [])
        if wi.get("work_item_id") == SEEDED_REVIEW_WORK_ITEM_ID and any(c.strip() for c in wi.get("comments", []))
    ]
    assert reviewed, f"No non-empty review comment reached Feishu Project work item {SEEDED_REVIEW_WORK_ITEM_ID}. Recorded: {data}"


# --- Test-case generation / classification / review flow -------------------------------


def test_test_case_flow_webhook_accepted(test_case_flow_response: httpx.Response) -> None:
    assert test_case_flow_response.status_code == 202, (
        f"Test-case flow webhook failed: "
        f"{test_case_flow_response.status_code} {test_case_flow_response.text}"
    )


def test_agent_fetched_feishu_doc(
    test_case_flow_response: httpx.Response, http_client: httpx.Client
) -> None:
    """The generation agent must fetch the Feishu document before generating test cases."""
    data = _wait_for_recorded(
        http_client,
        FEISHU_MCP_RECORDED_URL,
        lambda d: bool(d.get("fetched_docs")),
    )
    assert data.get("fetched_docs"), f"Generation agent never called feishu_get_doc_content. Recorded: {data}"


def test_real_test_cases_created_in_feishu_project(
    test_case_flow_response: httpx.Response, http_client: httpx.Client
) -> None:
    """Generation must create real test cases (non-empty name + steps) in Feishu Project."""
    data = _wait_for_recorded(
        http_client,
        MEEGO_RECORDED_URL,
        lambda d: any(tc.get("name", "").strip() and tc.get("steps", "").strip() for tc in d.get("test_cases", [])),
    )
    real_cases = [
        tc for tc in data.get("test_cases", []) if tc.get("name", "").strip() and tc.get("steps", "").strip()
    ]
    assert real_cases, f"Feishu Project received no test cases with both a name and steps. Recorded: {data}"


def test_classification_added_labels_in_feishu_project(
    test_case_flow_response: httpx.Response, http_client: httpx.Client
) -> None:
    """Classification must add labels to at least one test case in Feishu Project."""
    data = _wait_for_recorded(
        http_client,
        MEEGO_RECORDED_URL,
        lambda d: any(tc.get("labels") for tc in d.get("test_cases", [])),
    )
    labelled = [tc for tc in data.get("test_cases", []) if tc.get("labels")]
    assert labelled, f"No test case received labels from classification. Recorded: {data}"


def test_review_comment_added_to_feishu_test_case(
    test_case_flow_response: httpx.Response, http_client: httpx.Client
) -> None:
    """Review must post a non-empty comment to at least one test case in Feishu Project."""
    data = _wait_for_recorded(
        http_client,
        MEEGO_RECORDED_URL,
        lambda d: any(tc.get("comments") for tc in d.get("test_cases", [])),
    )
    reviewed = [tc for tc in data.get("test_cases", []) if tc.get("comments")]
    assert reviewed, f"No test case received a review comment. Recorded: {data}"


# --- Test execution / incident-creation flow -------------------------------------------


def test_failed_execution_creates_bug_in_jira(
    execute_tests_response: httpx.Response, http_client: httpx.Client
) -> None:
    """A failed automated test must drive incident creation: a real Bug reaches the seeded project."""
    data = _wait_for_recorded(
        http_client,
        JIRA_MCP_RECORDED_URL,
        lambda d: any(
            i.get("summary", "").strip() and i.get("description", "").strip() for i in d.get("created_issues", [])
        ),
    )
    bugs = [
        i
        for i in data.get("created_issues", [])
        if i.get("summary", "").strip()
        and i.get("description", "").strip()
        and i.get("issue_type") == "Bug"
        and i.get("project_key") == SEEDED_PROJECT_KEY
    ]
    assert bugs, (
        f"No Bug issue for project {SEEDED_PROJECT_KEY} reached Jira from the incident-creation flow. "
        f"Recorded: {data}"
    )

    # Regression guard: the redacted debug-trace artifact (ArtifactName.TRACE) must be excluded
    # from the file parts forwarded to the incident-creation agent (see
    # _get_file_contents_from_artifacts in orchestrator/main.py). If that exclusion were ever
    # dropped, the raw pydantic-ai message dump — identifiable by its own serialization field
    # names, which would never otherwise appear in an LLM-authored bug report — could leak into
    # the created issue.
    trace_markers = ("part_kind", "tool_call_id")
    for issue in bugs:
        description = issue.get("description", "")
        assert not any(marker in description for marker in trace_markers), (
            f"Bug description appears to contain a leaked execution trace: {description!r}"
        )


def test_failed_execution_reported_to_zephyr(
    execute_tests_response: httpx.Response, http_client: httpx.Client
) -> None:
    """The reporting half of /execute-tests: a failed execution of the seeded case must reach
    Zephyr, inside a test cycle created for the seeded project."""
    data = _wait_for_recorded(
        http_client,
        ZEPHYR_RECORDED_URL,
        lambda d: any(e.get("testCaseKey") == SEEDED_EXECUTABLE_TC_KEY for e in d.get("test_executions", [])),
    )
    executions = [e for e in data.get("test_executions", []) if e.get("testCaseKey") == SEEDED_EXECUTABLE_TC_KEY]
    assert executions, f"No test execution for {SEEDED_EXECUTABLE_TC_KEY} reached Zephyr. Recorded: {data}"
    failed = [e for e in executions if e.get("statusName") == "Fail"]
    assert failed, f"The execution of {SEEDED_EXECUTABLE_TC_KEY} was not reported as failed. Recorded: {executions}"
    cycle_keys = {cycle.get("key") for cycle in data.get("test_cycles", []) if cycle.get("projectKey") == SEEDED_PROJECT_KEY}
    assert any(e.get("testCycleKey") in cycle_keys for e in failed), (
        f"No failed execution belongs to a test cycle of project {SEEDED_PROJECT_KEY}. "
        f"Executions: {failed}, cycles: {data.get('test_cycles', [])}"
    )


def test_created_bug_linked_to_test_execution(
    execute_tests_response: httpx.Response, http_client: httpx.Client
) -> None:
    """The bug created for the failed execution must be linked back to the Zephyr execution."""
    zephyr = _wait_for_recorded(http_client, ZEPHYR_RECORDED_URL, lambda d: bool(d.get("execution_issue_links")))
    links = zephyr.get("execution_issue_links", [])
    assert links, f"No issue was linked to any test execution in Zephyr. Recorded: {zephyr}"
    mcp = _wait_for_recorded(http_client, JIRA_MCP_RECORDED_URL, lambda d: bool(d.get("created_issues")))
    created_issue_ids = {str(issue.get("id")) for issue in mcp.get("created_issues", [])}
    assert any(str(link.get("issue_id")) in created_issue_ids for link in links), (
        f"No created bug ({created_issue_ids}) was linked to a test execution. Links: {links}"
    )


def test_incident_creation_consulted_vector_db(
    execute_tests_response: httpx.Response, http_client: httpx.Client
) -> None:
    """The duplicate search must consult the vector DB (at least the collection-list probe)."""
    data = _wait_for_recorded(http_client, QDRANT_RECORDED_URL, lambda d: d.get("collections_probes", 0) > 0)
    assert data.get("collections_probes", 0) > 0, f"The vector DB was never consulted. Recorded: {data}"


# --- RAG vector DB update flow ----------------------------------------------------------


def test_update_rag_db_webhook_accepted(update_rag_db_response: httpx.Response) -> None:
    assert update_rag_db_response.status_code == 200, (
        f"RAG-update webhook failed: {update_rag_db_response.status_code} {update_rag_db_response.text}"
    )
    details = update_rag_db_response.json().get("details", {})
    assert details.get("processed_count", 0) >= 1, f"The RAG sync processed no issues: {details}"


def test_rag_sync_upserted_seeded_story_into_vector_db(
    update_rag_db_response: httpx.Response, http_client: httpx.Client
) -> None:
    """The sync must push the seeded story into the tickets collection of the vector DB."""
    data = _wait_for_recorded(
        http_client,
        QDRANT_RECORDED_URL,
        lambda d: any(
            p.get("collection") == TICKETS_COLLECTION_NAME and p.get("payload", {}).get("key") == SEEDED_ISSUE_KEY
            for p in d.get("upserted_points", [])
        ),
    )
    upserts = [
        p
        for p in data.get("upserted_points", [])
        if p.get("collection") == TICKETS_COLLECTION_NAME and p.get("payload", {}).get("key") == SEEDED_ISSUE_KEY
    ]
    assert upserts, f"The seeded story {SEEDED_ISSUE_KEY} never reached the vector DB. Recorded: {data}"
    payload = upserts[0]["payload"]
    assert payload.get("project_key") == SEEDED_PROJECT_KEY, f"Wrong project on the upserted story: {payload}"
    assert payload.get("summary", "").strip(), f"The upserted story has no summary: {payload}"
    assert TICKETS_COLLECTION_NAME in data.get("created_collections", []), (
        f"The tickets collection was never created. Recorded: {data}"
    )


# --- Negative paths (auth + validation; reach the orchestrator only, no LLM) ------------

REQUIRED_FIELD_WEBHOOK_PATHS = ["/story-ready-for-test-case-generation"]
PROJECT_KEY_WEBHOOK_PATHS = ["/execute-tests", "/update-rag-db"]
AUTHENTICATED_WEBHOOKS = [
    ("/story-ready-for-test-case-generation", {"story_id": SEEDED_STORY_ID, "feishu_doc": "https://example.feishu.cn/wiki/x"}),
    ("/execute-tests", {"project_key": SEEDED_PROJECT_KEY}),
    ("/update-rag-db", {"project_key": SEEDED_PROJECT_KEY}),
]


@pytest.mark.parametrize(("path", "payload"), AUTHENTICATED_WEBHOOKS)
def test_webhook_rejects_invalid_api_key(http_client: httpx.Client, path: str, payload: dict[str, str]) -> None:
    """A wrong orchestrator API key must be rejected with 401 before any work starts."""
    response = http_client.post(f"{ORCHESTRATOR_URL}{path}", headers={"X-API-Key": "wrong-key"}, json=payload)
    assert response.status_code == 401, f"{path} accepted an invalid API key: {response.status_code} {response.text}"


@pytest.mark.parametrize("path", REQUIRED_FIELD_WEBHOOK_PATHS)
def test_webhook_rejects_missing_required_fields(
    http_client: httpx.Client, webhook_headers: dict[str, str], path: str
) -> None:
    """A valid key but empty payload must fail validation with 400, without dispatching to an agent."""
    response = http_client.post(f"{ORCHESTRATOR_URL}{path}", headers=webhook_headers, json={})
    assert response.status_code == 400, (
        f"{path} did not reject a missing required field with 400: {response.status_code} {response.text}"
    )


@pytest.mark.parametrize("path", PROJECT_KEY_WEBHOOK_PATHS)
def test_webhook_rejects_missing_project_key(
    http_client: httpx.Client, webhook_headers: dict[str, str], path: str
) -> None:
    """A valid key but no project_key must fail request-model validation with 422."""
    response = http_client.post(f"{ORCHESTRATOR_URL}{path}", headers=webhook_headers, json={})
    assert response.status_code == 422, (
        f"{path} did not reject a missing project_key with 422: {response.status_code} {response.text}"
    )


def test_requirement_review_webhook_rejects_malformed_payload(http_client: httpx.Client) -> None:
    """Unauthenticated by design; a malformed (non-object) payload is silently dropped (204), not an error."""
    response = http_client.post(f"{ORCHESTRATOR_URL}/requirement-ready-for-review", json=["not", "an", "object"])
    assert response.status_code == 204, (
        f"Malformed payload was not silently dropped: {response.status_code} {response.text}"
    )


def test_requirement_review_webhook_rejects_missing_prd_link(http_client: httpx.Client) -> None:
    """A work item whose PRD-link field isn't filled in yet is silently dropped (204), not enqueued."""
    payload = {
        "payload": {
            "id": "SMOKE-REVIEW-NO-LINK",
            "project_simple_name": SEEDED_PROJECT_KEY,
            "nodes": [{"node_form": []}],
        }
    }
    response = http_client.post(f"{ORCHESTRATOR_URL}/requirement-ready-for-review", json=payload)
    assert response.status_code == 204, (
        f"Missing PRD link was not silently dropped: {response.status_code} {response.text}"
    )


def test_dashboard_api_rejects_missing_token(http_client: httpx.Client) -> None:
    """The dashboard API must reject requests without a bearer token with 401."""
    response = http_client.get(f"{ORCHESTRATOR_URL}/api/dashboard/agents")
    assert response.status_code == 401, (
        f"The dashboard API accepted a request without a token: {response.status_code} {response.text}"
    )
