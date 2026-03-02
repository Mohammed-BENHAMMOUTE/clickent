import json
from typing import Any
from logging import getLogger

from fastapi import APIRouter

from src.services.clickup import (
    get_clickup_task_details,
    is_eligible_task,
    is_status_transition_to_in_progress,
    is_task_open,
    move_task_to_in_progress,
)
from src.services.agent import enqueue_task_for_agent

logger = getLogger(name=__name__)

router = APIRouter()


@router.post("/webhook")
async def clickup_webhook(payload: dict):
    event = payload.get("event", "unknown")
    task_id = payload.get("task_id")
    logger.info("Received ClickUp webhook event=%s task_id=%s", event, task_id)
    logger.info("Incoming webhook payload=%s", payload)

    if event not in {"taskCreated", "taskUpdated"}:
        return {"status": "ignored", "reason": "unsupported_event"}

    if not task_id:
        return {"status": "ok"}

    task_details_from_api: dict[str, Any] = {}
    try:
        task_details_from_api = await get_clickup_task_details(str(task_id))
    except Exception as e:
        logger.exception(
            "Failed to fetch task details directly from ClickUp API for task_id=%s error=%s",
            task_id,
            e,
        )
        return {"status": "error", "detail": "could not fetch task details"}

    if not is_eligible_task(task_details_from_api):
        logger.info(
            "Ignoring webhook because task is not eligible: needs Agent tag, assignee match, and open/in progress status. task_id=%s",
            task_id,
        )
        return {
            "status": "ignored",
            "reason": "task_not_eligible",
            "task_id": task_id,
        }

    if is_status_transition_to_in_progress(payload):
        logger.info(
            "Ignoring taskUpdated caused by status transition to in progress to avoid duplicate processing: task_id=%s",
            task_id,
        )
        return {
            "status": "ignored",
            "reason": "status_transition_to_in_progress",
            "task_id": task_id,
        }

    # Use REST API task details directly — no need to call the agent just to fetch them
    task_details_for_agent = json.dumps(task_details_from_api)

    move_status_ok = False
    move_status_result = "not_attempted"
    if task_details_from_api and is_task_open(task_details_from_api):
        try:
            move_status_ok, move_status_result = await move_task_to_in_progress(
                task_details_from_api
            )
            logger.info(
                "Move task to in progress result: task_id=%s success=%s result=%s",
                task_id,
                move_status_ok,
                move_status_result,
            )
        except Exception as e:
            move_status_result = "failed"
            logger.exception(
                "Failed moving task to in progress task_id=%s error=%s",
                task_id,
                e,
            )

    try:
        await enqueue_task_for_agent(
            task_id=str(task_id),
            event=str(event),
            task_details_for_agent=task_details_for_agent,
        )

    except Exception as e:
        logger.exception(
            "Failed to enqueue task for Cursor Agent processing task_id=%s error=%s",
            task_id,
            e,
        )
        return {"status": "error", "detail": "could not enqueue task"}

    return {
        "status": "ok",
        "task_id": task_id,
        "moved_to_in_progress": move_status_ok,
        "move_status_result": move_status_result,
        "enqueued": True,
    }
