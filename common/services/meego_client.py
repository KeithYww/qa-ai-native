# SPDX-FileCopyrightText: 2025-2026 Taras Paruta (partarstu@gmail.com)
#
# SPDX-License-Identifier: AGPL-3.0-only

import time

import httpx

import config
from common import utils
from common.models import TestCase, TestExecutionResult
from common.services.test_management_base import TestManagementClientBase

logger = utils.get_logger(__name__)

# Feishu Project (Meego) work item type key for test cases in the target space.
_TEST_CASE_TYPE_KEY = "test_cases"

# Feishu Project field keys for the test_cases work item type.
_FIELD_STEPS = "field_023f96"       # 执行步骤 (multi-text)
_FIELD_EXPECTED = "field_2c7371"    # 预期结果 (multi-text)
_FIELD_LABELS = "field_65e1cc"      # 标签 (multi-select)

# Status option IDs for the test_cases work item type.
_STATUS_OPTIONS: dict[str, str] = {
    "not_reviewed": "not_reviewed",
    "review_passed": "review_passed",
    "review_failed": "review_failed",
    "systemended": "systemEnded",
}


class MeegoClient(TestManagementClientBase):
    """Client for Feishu Project (Meego) Open API."""

    def __init__(self, project_key: str | None = None) -> None:
        self._base_url = (config.MEEGO_BASE_URL or "").rstrip("/")
        if not self._base_url:
            raise ValueError("MEEGO_BASE_URL is not configured.")
        self._plugin_id = config.MEEGO_PLUGIN_ID
        self._plugin_secret = config.MEEGO_PLUGIN_SECRET
        self._default_project_key = project_key or config.MEEGO_PROJECT_KEY
        self._user_key = config.MEEGO_USER_KEY
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _get_token(self) -> str:
        """Return a valid plugin_token, refreshing if within 60 s of expiry."""
        if self._token and time.monotonic() < self._token_expires_at - 60:
            return self._token
        if not self._plugin_id or not self._plugin_secret:
            raise ValueError("MEEGO_PLUGIN_ID and MEEGO_PLUGIN_SECRET must be set.")
        with httpx.Client() as client:
            response = client.post(
                f"{self._base_url}/open_api/authing/plugin_token",
                json={"plugin_id": self._plugin_id, "plugin_secret": self._plugin_secret, "type": 1},
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
        self._token = data["data"]["token"]
        # Feishu Project tokens expire in ~7200 s; fall back to 7200 if field absent.
        ttl = data["data"].get("expire_time", 7200)
        self._token_expires_at = time.monotonic() + ttl
        return self._token

    def _headers(self) -> dict[str, str]:
        headers = {"X-PLUGIN-TOKEN": self._get_token(), "Content-Type": "application/json"}
        if self._user_key:
            headers["X-USER-KEY"] = self._user_key
        return headers

    @staticmethod
    def _validate_response(response: httpx.Response) -> dict:
        response.raise_for_status()
        data = response.json()
        if data.get("code", 0) != 0:
            raise RuntimeError(f"Meego API request failed: {data}")
        return data

    def _post(self, path: str, payload: dict) -> dict:
        with httpx.Client() as client:
            response = client.post(
                f"{self._base_url}{path}",
                headers=self._headers(),
                json=payload,
                timeout=30,
            )
            return self._validate_response(response)

    def _patch(self, path: str, payload: dict) -> dict:
        with httpx.Client() as client:
            response = client.patch(
                f"{self._base_url}{path}",
                headers=self._headers(),
                json=payload,
                timeout=30,
            )
            return self._validate_response(response)

    # ------------------------------------------------------------------
    # TestManagementClientBase implementation
    # ------------------------------------------------------------------

    def create_test_cases(self, test_cases: list[TestCase], project_key: str, user_story_id: str) -> list[str]:
        """Create test case work items in Feishu Project and return their IDs."""
        created_ids: list[str] = []
        for tc in test_cases:
            steps_text = "\n".join(
                f"{i}. {step.action}" + (f"\n   Data: {', '.join(step.test_data)}" if step.test_data else "")
                for i, step in enumerate(tc.steps, start=1)
            )
            expected_text = "\n".join(step.expected_results for step in tc.steps if step.expected_results)
            fields = [
                {"field_key": "name", "field_value": tc.name},
                {"field_key": "description", "field_value": tc.preconditions or ""},
                {"field_key": _FIELD_STEPS, "field_value": steps_text},
                {"field_key": _FIELD_EXPECTED, "field_value": expected_text},
            ]
            payload = {
                "work_item_type_key": _TEST_CASE_TYPE_KEY,
                "fields": fields,
            }
            logger.info(f"Creating test case '{tc.name}' in Feishu Project space '{project_key}'")
            data = self._post(f"/open_api/{project_key}/work_item/create", payload)
            work_item_id = str(data.get("data", {}).get("work_item_id", ""))
            if work_item_id:
                logger.info(f"Created test case work item {work_item_id} for '{tc.name}'")
                created_ids.append(work_item_id)
            else:
                logger.warning(f"No work_item_id in response for test case '{tc.name}': {data}")
        return created_ids

    def add_labels_to_test_case(self, test_case_key: str, labels: list[str]) -> None:
        """Add classification labels to a test case work item."""
        payload = {"fields": [{"field_key": _FIELD_LABELS, "field_value": labels}]}
        self._patch(f"/open_api/{self._default_project_key}/work_item/{test_case_key}", payload)
        logger.info(f"Added labels {labels} to test case {test_case_key}")

    def add_test_case_review_comment(self, test_case_key: str, comment: str) -> None:
        """Add a review comment to a test case work item."""
        payload = {"content": comment, "type": 1}
        self._post(f"/open_api/{self._default_project_key}/work_item/{test_case_key}/comment/create", payload)
        logger.info(f"Added review comment to test case {test_case_key}")

    def add_work_item_comment(self, project_key: str, work_item_type_key: str, work_item_id: str, content: str) -> None:
        """Add a comment to any work item (e.g. a story), addressed by ID in the request body.

        This is a distinct Feishu Project (Meego) endpoint from add_test_case_review_comment
        (which addresses the work item by ID in the URL path).
        """
        payload = {"work_item_id": work_item_id, "work_item_type_key": work_item_type_key, "content": content}
        self._post(f"/open_api/{project_key}/work_item/comment/create", payload)
        logger.info(f"Added comment to work item {work_item_id}")

    def change_test_case_status(self, project_key: str, test_case_key: str, new_status: str) -> None:
        """Transition a test case work item to the given status."""
        status_id = _STATUS_OPTIONS.get(new_status.lower().replace(" ", "_"))
        if not status_id:
            # Fall back to case-insensitive match of the raw value.
            status_id = next(
                (v for k, v in _STATUS_OPTIONS.items() if k.replace("_", " ") == new_status.lower()),
                new_status,
            )
        payload = {"transition_status_id": status_id}
        self._post(f"/open_api/{project_key}/work_item/{test_case_key}/transition_state", payload)
        logger.info(f"Changed status of {test_case_key} to '{new_status}'")

    # ------------------------------------------------------------------
    # Methods used only by /execute-tests flow (not in scope)
    # ------------------------------------------------------------------

    def fetch_ready_for_execution_test_cases_by_labels(
        self, project_key: str, target_labels: list[str], max_results: int = 100
    ) -> dict[str, list[TestCase]]:
        raise NotImplementedError("fetch_ready_for_execution_test_cases_by_labels not supported by MeegoClient")

    def create_test_execution(
        self, test_execution_results: list[TestExecutionResult], project_key: str, version_id: str
    ) -> None:
        raise NotImplementedError("create_test_execution not supported by MeegoClient")

    def create_test_plan(self, project_key: str, name: str, description: str | None = None) -> str:
        raise NotImplementedError("create_test_plan not supported by MeegoClient")

    def fetch_test_case_by_key(self, test_case_key: str) -> TestCase:
        raise NotImplementedError("fetch_test_case_by_key not supported by MeegoClient")

    def fetch_linked_issues(self, test_case_key: str) -> list[dict]:
        raise NotImplementedError("fetch_linked_issues not supported by MeegoClient")

    def link_issue_to_test_case(self, test_case_key: str, issue_id: int, link_type: str) -> None:
        raise NotImplementedError("link_issue_to_test_case not supported by MeegoClient")
