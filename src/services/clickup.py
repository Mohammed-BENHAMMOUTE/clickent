"""ClickUp API: webhooks, task details, status transitions, task inspection."""

import logging
from typing import Any, Callable

import httpx

from src.services.config import (
    CLICKUP_ACCESS_TOKEN,
    CLICKUP_ASSIGNEE_ID,
    CLICKUP_ASSIGNEE_NAME,
    CLICKUP_WORKSPACE_ID,
)
from src.models.webhook import WebhookRegistrationResponse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def _clickup_headers() -> dict[str, str]:
    return {
        "Authorization": CLICKUP_ACCESS_TOKEN,
        "accept": "application/json",
        "content-type": "application/json",
    }


# ---------------------------------------------------------------------------
# Webhook registration / deletion
# ---------------------------------------------------------------------------
async def get_existing_clickup_webhook(
    endpoint: str,
) -> WebhookRegistrationResponse | None:
    url = f"https://api.clickup.com/api/v2/team/{CLICKUP_WORKSPACE_ID}/webhook"
    async with httpx.AsyncClient() as client:
        response = await client.get(
            url,
            headers={
                "Authorization": CLICKUP_ACCESS_TOKEN,
                "accept": "application/json",
            },
        )
        response.raise_for_status()

    for webhook in response.json().get("webhooks", []):
        if webhook.get("endpoint") == endpoint:
            return WebhookRegistrationResponse.model_validate(
                {"id": webhook["id"], "webhook": webhook}
            )
    return None


async def register_clickup_webhook(endpoint: str) -> WebhookRegistrationResponse:
    url = f"https://api.clickup.com/api/v2/team/{CLICKUP_WORKSPACE_ID}/webhook"
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                url,
                json={
                    "endpoint": endpoint,
                    "events": ["taskCreated", "taskUpdated"],
                    "status": "active",
                },
                headers={
                    "Authorization": CLICKUP_ACCESS_TOKEN,
                    "accept": "application/json",
                    "content-type": "application/json",
                },
            )
            response.raise_for_status()
            return WebhookRegistrationResponse.model_validate(response.json())
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                existing = await get_existing_clickup_webhook(endpoint)
                if existing:
                    logger.info(
                        "Webhook already exists. Reusing webhook %s.", existing.id,
                    )
                    return existing
            raise


async def delete_clickup_webhook(webhook_id: str) -> None:
    url = f"https://api.clickup.com/api/v2/webhook/{webhook_id}"
    async with httpx.AsyncClient() as client:
        response = await client.delete(
            url,
            headers={
                "Authorization": CLICKUP_ACCESS_TOKEN,
                "accept": "application/json",
            },
        )
        response.raise_for_status()


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------
def _normalize_status(status: str) -> str:
    return status.strip().lower().replace("_", " ").replace("-", " ")


def _is_in_progress_status(status: str) -> bool:
    return _normalize_status(status) in {"in progress", "progress"}


def _is_review_status(status: str) -> bool:
    return _normalize_status(status) in {
        "review", "in review", "awaiting review", "code review",
    }


def _is_closed_status(status: str) -> bool:
    return _normalize_status(status) in {"closed", "done", "complete", "completed"}


def _is_blocked_status(status: str) -> bool:
    return _normalize_status(status) in {"blocked"}


def _is_open_status(status: str) -> bool:
    return _normalize_status(status) == "open"


# Maps agent outcome keywords -> status matcher functions
OUTCOME_STATUS_MAP: dict[str, Callable[[str], bool]] = {
    "done": _is_closed_status,
    "review": _is_review_status,
    "blocked": _is_blocked_status,
}


# ---------------------------------------------------------------------------
# Task inspection
# ---------------------------------------------------------------------------
def is_task_open(task: dict[str, Any]) -> bool:
    status_obj = task.get("status", {}) or {}
    status_type = _normalize_status(str(status_obj.get("type", "")))
    status_name = _normalize_status(str(status_obj.get("status", "")))
    return _is_open_status(status_name) or status_type == "open"


def has_agent_tag(task: dict[str, Any]) -> bool:
    for tag in task.get("tags", []) or []:
        if str(tag.get("name", "")).strip().casefold() == "agent":
            return True
    return False


def is_task_open_or_in_progress(task: dict[str, Any]) -> bool:
    status_name = str((task.get("status", {}) or {}).get("status", ""))
    return is_task_open(task) or _is_in_progress_status(status_name)


def _normalize_identity(value: str) -> str:
    return value.strip().casefold()


def is_task_assigned_to_me(task: dict[str, Any]) -> bool:
    assignees = task.get("assignees", []) or []
    configured_name = _normalize_identity(CLICKUP_ASSIGNEE_NAME)
    configured_id = CLICKUP_ASSIGNEE_ID.strip()

    if not configured_name and not configured_id:
        logger.warning(
            "No assignee identity configured. "
            "Set CLICKUP_ASSIGNEE_NAME or CLICKUP_ASSIGNEE_ID."
        )
        return False

    for assignee in assignees:
        aid = str(assignee.get("id", "")).strip()
        uname = _normalize_identity(str(assignee.get("username", "")))
        email = _normalize_identity(str(assignee.get("email", "")))

        if configured_id and aid == configured_id:
            return True
        if configured_name and (
            configured_name == uname
            or configured_name == email
            or configured_name in uname
            or configured_name in email
        ):
            return True
    return False


def is_eligible_task(task: dict[str, Any]) -> bool:
    tag_ok = has_agent_tag(task)
    assignee_ok = is_task_assigned_to_me(task)
    status_ok = is_task_open_or_in_progress(task)

    logger.debug(
        "Eligibility check: task_id=%s tag=%s assignee=%s status=%s",
        task.get("id", "?"), tag_ok, assignee_ok, status_ok,
    )
    return tag_ok and assignee_ok and status_ok


def is_status_transition_to_in_progress(payload: dict[str, Any]) -> bool:
    if payload.get("event") != "taskUpdated":
        return False
    for item in payload.get("history_items", []) or []:
        if str(item.get("field", "")).strip().lower() != "status":
            continue
        after_status = str((item.get("after") or {}).get("status", ""))
        if _is_in_progress_status(after_status):
            return True
    return False


# ---------------------------------------------------------------------------
# ClickUp REST API — task operations
# ---------------------------------------------------------------------------
async def get_clickup_task_details(task_id: str) -> dict[str, Any]:
    url = f"https://api.clickup.com/api/v2/task/{task_id}"
    logger.info("Fetching ClickUp task details: task_id=%s", task_id)
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=_clickup_headers())
        response.raise_for_status()
        return response.json()


async def move_task_to_in_progress(task: dict[str, Any]) -> tuple[bool, str]:
    task_id = str(task.get("id", ""))
    if not task_id:
        return (False, "missing_task_id")

    current_status = str(task.get("status", {}).get("status", ""))
    if current_status and _is_in_progress_status(current_status):
        return (True, "already_in_progress")

    list_id = str(task.get("list", {}).get("id", ""))
    if not list_id:
        return (False, "missing_list_id")

    logger.info(
        "Status transition -> in-progress: task_id=%s current=%s list_id=%s",
        task_id, current_status, list_id,
    )

    list_url = f"https://api.clickup.com/api/v2/list/{list_id}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(list_url, headers=_clickup_headers())
        resp.raise_for_status()

    target_status: str | None = None
    for s in resp.json().get("statuses", []):
        if _is_in_progress_status(str(s.get("status", ""))):
            target_status = str(s["status"])
            break

    if not target_status:
        logger.warning("No in-progress status found: task=%s list=%s", task_id, list_id)
        return (False, "in_progress_status_not_found")

    update_url = f"https://api.clickup.com/api/v2/task/{task_id}"
    async with httpx.AsyncClient() as client:
        resp = await client.put(
            update_url, headers=_clickup_headers(), json={"status": target_status},
        )
        resp.raise_for_status()

    logger.info("Task moved to in-progress: task_id=%s status=%s", task_id, target_status)
    return (True, f"moved_to_{target_status}")


async def _find_status_in_list(
    list_id: str, matcher: Callable[[str], bool],
) -> str | None:
    """Return the first matching status name from a list's statuses."""
    list_url = f"https://api.clickup.com/api/v2/list/{list_id}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(list_url, headers=_clickup_headers())
        resp.raise_for_status()
    for s in resp.json().get("statuses", []):
        if matcher(str(s.get("status", ""))):
            return str(s["status"])
    return None


async def update_task_status(
    task_id: str,
    list_id: str,
    matcher: Callable[[str], bool],
    label: str,
) -> tuple[bool, str]:
    """Move a task to the first status in its list that matches *matcher*."""
    target = await _find_status_in_list(list_id, matcher)
    if not target:
        logger.warning("No '%s' status in list %s for task %s", label, list_id, task_id)
        return (False, f"{label}_status_not_found")

    url = f"https://api.clickup.com/api/v2/task/{task_id}"
    async with httpx.AsyncClient() as client:
        resp = await client.put(url, headers=_clickup_headers(), json={"status": target})
        resp.raise_for_status()

    logger.info("Task %s -> %s (%s)", task_id, target, label)
    return (True, f"moved_to_{target}")
