import os
import asyncio
import json
import logging
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from contextlib import asynccontextmanager
from logging import getLogger

from src.controllers.ws import ConnectionManager
from src.models.webhook import WebhookRegistrationResponse
from fastapi.middleware.cors import CORSMiddleware


logger = getLogger(name=__name__)

load_dotenv()

CLICKUP_ACCESS_TOKEN = os.getenv("CLICKUP_ACCESS_TOKEN", "")
CLICKUP_WORKSPACE_ID = os.getenv("CLICKUP_WORKSPACE_ID", "")
CLICKUP_ASSIGNEE_NAME = os.getenv("CLICKUP_ASSIGNEE_NAME", "")
CLICKUP_ASSIGNEE_ID = os.getenv("CLICKUP_ASSIGNEE_ID", "")
PUBLIC_URL = os.getenv("PUBLIC_URL", "")
AGENT_COMMAND = os.getenv("AGENT_COMMAND", "agent")
AGENT_MODEL = os.getenv("AGENT_MODEL", "")
AGENT_TIMEOUT_SECONDS = int(os.getenv("AGENT_TIMEOUT_SECONDS", "120"))
AGENT_CLICKUP_MCP_IDENTIFIER = os.getenv("AGENT_CLICKUP_MCP_IDENTIFIER", "clickup")
AGENT_QUEUE_MAXSIZE = int(os.getenv("AGENT_QUEUE_MAXSIZE", "200"))
TARGET_REPO_PATH = os.getenv("TARGET_REPO_PATH", "")
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


def configure_logging() -> None:
    level = getattr(logging, LOG_LEVEL, logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    if not root_logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(level)
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s"
        )
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)

    logger.setLevel(level)


configure_logging()

manager = ConnectionManager()
webhook_credentials: WebhookRegistrationResponse | None = None
task_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=AGENT_QUEUE_MAXSIZE)
agent_worker_task: asyncio.Task | None = None


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

    webhooks = response.json().get("webhooks", [])
    for webhook in webhooks:
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
                existing_webhook = await get_existing_clickup_webhook(endpoint)
                if existing_webhook:
                    logger.info(
                        "Webhook already exists for this endpoint. "
                        f"Reusing webhook {existing_webhook.id}."
                    )
                    return existing_webhook
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


def _clickup_headers() -> dict[str, str]:
    return {
        "Authorization": CLICKUP_ACCESS_TOKEN,
        "accept": "application/json",
        "content-type": "application/json",
    }


def _normalize_status(status: str) -> str:
    return status.strip().lower().replace("_", " ").replace("-", " ")


def _is_in_progress_status(status: str) -> bool:
    normalized = _normalize_status(status)
    return normalized in {"in progress", "progress"}


def _is_review_status(status: str) -> bool:
    normalized = _normalize_status(status)
    return normalized in {"review", "in review", "awaiting review", "code review"}


def _is_closed_status(status: str) -> bool:
    normalized = _normalize_status(status)
    return normalized in {"closed", "done", "complete", "completed"}


def _is_blocked_status(status: str) -> bool:
    normalized = _normalize_status(status)
    return normalized in {"blocked"}


def _is_open_status(status: str) -> bool:
    normalized = _normalize_status(status)
    return normalized == "open"


def is_task_open(task: dict[str, Any]) -> bool:
    status_obj = task.get("status", {}) or {}
    status_type = _normalize_status(str(status_obj.get("type", "")))
    status_name = _normalize_status(str(status_obj.get("status", "")))

    # Strict mode: only "open" status is accepted.
    if _is_open_status(status_name):
        return True
    if status_type == "open":
        return True

    return False


def has_agent_tag(task: dict[str, Any]) -> bool:
    tags = task.get("tags", []) or []
    for tag in tags:
        tag_name = str(tag.get("name", "")).strip().casefold()
        if tag_name == "agent":
            return True
    return False


def is_task_open_or_in_progress(task: dict[str, Any]) -> bool:
    status_obj = task.get("status", {}) or {}
    status_name = str(status_obj.get("status", ""))
    return is_task_open(task) or _is_in_progress_status(status_name)


def is_eligible_task(task: dict[str, Any]) -> bool:
    tag_ok = has_agent_tag(task)
    assignee_ok = is_task_assigned_to_me(task)
    status_ok = is_task_open_or_in_progress(task)
    task_id = task.get("id", "?")
    tags = [str(t.get("name", "")) for t in (task.get("tags", []) or [])]
    status = str((task.get("status", {}) or {}).get("status", ""))
    assignees = [str(a.get("username", "")) for a in (task.get("assignees", []) or [])]
    logger.debug(
        "Eligibility check: task_id=%s tag_ok=%s (tags=%s) assignee_ok=%s (assignees=%s) "
        "status_ok=%s (status=%s)",
        task_id, tag_ok, tags, assignee_ok, assignees, status_ok, status,
    )
    return tag_ok and assignee_ok and status_ok


def is_status_transition_to_in_progress(payload: dict[str, Any]) -> bool:
    if payload.get("event") != "taskUpdated":
        return False
    history_items = payload.get("history_items", []) or []
    for item in history_items:
        if str(item.get("field", "")).strip().lower() != "status":
            continue
        after_status = str((item.get("after") or {}).get("status", ""))
        if _is_in_progress_status(after_status):
            return True
    return False


def _normalize_identity(value: str) -> str:
    return value.strip().casefold()


def is_task_assigned_to_me(task: dict[str, Any]) -> bool:
    assignees = task.get("assignees", []) or []
    configured_name = _normalize_identity(CLICKUP_ASSIGNEE_NAME)
    configured_id = CLICKUP_ASSIGNEE_ID.strip()

    if not configured_name and not configured_id:
        logger.warning(
            "No assignee identity configured. Set CLICKUP_ASSIGNEE_NAME or CLICKUP_ASSIGNEE_ID."
        )
        return False

    for assignee in assignees:
        assignee_id = str(assignee.get("id", "")).strip()
        assignee_username = _normalize_identity(str(assignee.get("username", "")))
        assignee_email = _normalize_identity(str(assignee.get("email", "")))

        if configured_id and assignee_id == configured_id:
            return True
        if configured_name and (
            configured_name == assignee_username
            or configured_name == assignee_email
            or configured_name in assignee_username
            or configured_name in assignee_email
        ):
            return True

    return False


async def get_clickup_task_details(task_id: str) -> dict[str, Any]:
    url = f"https://api.clickup.com/api/v2/task/{task_id}"
    logger.info("Fetching ClickUp task details via REST API: task_id=%s", task_id)
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
        "Attempting status transition to in progress: task_id=%s current_status=%s list_id=%s",
        task_id,
        current_status,
        list_id,
    )

    list_url = f"https://api.clickup.com/api/v2/list/{list_id}"
    async with httpx.AsyncClient() as client:
        list_response = await client.get(list_url, headers=_clickup_headers())
        list_response.raise_for_status()
        list_data = list_response.json()

    statuses = list_data.get("statuses", [])
    target_status: str | None = None
    for status_obj in statuses:
        status_name = str(status_obj.get("status", ""))
        if _is_in_progress_status(status_name):
            target_status = status_name
            break

    if not target_status:
        logger.warning(
            "No in progress status found for task list: task_id=%s list_id=%s",
            task_id,
            list_id,
        )
        return (False, "in_progress_status_not_found")

    update_url = f"https://api.clickup.com/api/v2/task/{task_id}"
    async with httpx.AsyncClient() as client:
        update_response = await client.put(
            update_url,
            headers=_clickup_headers(),
            json={"status": target_status},
        )
        update_response.raise_for_status()

    logger.info(
        "Task moved to in progress: task_id=%s new_status=%s",
        task_id,
        target_status,
    )
    return (True, f"moved_to_{target_status}")


async def _find_status_in_list(
    list_id: str, matcher: callable
) -> str | None:
    """Look up a status name from the list's configured statuses."""
    list_url = f"https://api.clickup.com/api/v2/list/{list_id}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(list_url, headers=_clickup_headers())
        resp.raise_for_status()
    for s in resp.json().get("statuses", []):
        if matcher(str(s.get("status", ""))):
            return str(s["status"])
    return None


async def update_task_status(
    task_id: str, list_id: str, matcher: callable, label: str
) -> tuple[bool, str]:
    """Move a task to the first status in its list that matches `matcher`."""
    target = await _find_status_in_list(list_id, matcher)
    if not target:
        logger.warning(
            "No '%s' status found in list %s for task %s",
            label, list_id, task_id,
        )
        return (False, f"{label}_status_not_found")

    url = f"https://api.clickup.com/api/v2/task/{task_id}"
    async with httpx.AsyncClient() as client:
        resp = await client.put(url, headers=_clickup_headers(), json={"status": target})
        resp.raise_for_status()

    logger.info("Task %s moved to %s (%s)", task_id, target, label)
    return (True, f"moved_to_{target}")


# Maps agent outcome keywords → status matcher functions
OUTCOME_STATUS_MAP: dict[str, callable] = {
    "done": _is_closed_status,
    "review": _is_review_status,
    "blocked": _is_blocked_status,
}

VALID_OUTCOMES = {"done", "review", "blocked", "in_progress"}


def _parse_agent_outcome(agent_output: str) -> str:
    """Extract the OUTCOME: <value> line from agent output."""
    import re

    for line in reversed(agent_output.splitlines()):
        match = re.match(r"^\s*OUTCOME:\s*(\w+)", line, re.IGNORECASE)
        if match:
            outcome = match.group(1).strip().lower()
            if outcome in VALID_OUTCOMES:
                return outcome
    # Fallback: try to infer from keywords in the last few lines
    tail = agent_output[-500:].lower()
    if "pull request" in tail or "PR" in agent_output[-500:]:
        return "review"
    if "completed" in tail or "task is done" in tail:
        return "done"
    if "blocked" in tail or "cannot proceed" in tail:
        return "blocked"
    return "in_progress"


def _extract_list_id(task_details_json: str) -> str | None:
    """Pull the list ID from the task details JSON string."""
    try:
        data = json.loads(task_details_json)
        return str(data.get("list", {}).get("id", "")) or None
    except (json.JSONDecodeError, AttributeError):
        return None


async def run_agent_prompt(prompt: str) -> str:
    command = [
        AGENT_COMMAND,
        "--print",
        "--output-format",
        "text",
        "--force",
        "--approve-mcps",
        "--workspace",
        TARGET_REPO_PATH or os.getcwd(),
    ]
    if AGENT_MODEL:
        command.extend(["--model", AGENT_MODEL])
    command.append(prompt)
    logger.info(
        "Running Cursor Agent command: model=%s timeout=%ss",
        AGENT_MODEL or "default",
        AGENT_TIMEOUT_SECONDS,
    )

    agent_env = os.environ.copy()
    agent_env["GITHUB_TOKEN"] = os.getenv("GITHUB_TOKEN", "")

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=TARGET_REPO_PATH or None,
        env=agent_env,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=AGENT_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as e:
        process.kill()
        await process.wait()
        logger.error("Cursor Agent command timed out after %ss", AGENT_TIMEOUT_SECONDS)
        raise RuntimeError(
            f"Agent timed out after {AGENT_TIMEOUT_SECONDS}s."
        ) from e

    if process.returncode != 0:
        logger.error("Cursor Agent command failed with return code=%s", process.returncode)
        raise RuntimeError(
            "Agent execution failed. "
            f"stderr={stderr.decode('utf-8', errors='ignore').strip()}"
        )

    output = stdout.decode("utf-8", errors="ignore").strip()
    if not output:
        raise RuntimeError("Agent returned empty output.")
    logger.info("Cursor Agent command completed successfully.")
    return output


async def get_clickup_task_details_via_agent(task_id: str) -> str:
    prompt = (
        "Use the configured ClickUp MCP server "
        f"('{AGENT_CLICKUP_MCP_IDENTIFIER}') to fetch the full task details for task ID "
        f"'{task_id}'. Return only a concise JSON object with keys: "
        "id, name, description, status, assignees, url."
    )
    return await run_agent_prompt(prompt)


async def enqueue_task_for_agent(
    task_id: str,
    event: str,
    task_details_for_agent: str,
) -> None:
    queue_item = {
        "task_id": task_id,
        "event": event,
        "task_details": task_details_for_agent,
    }
    await task_queue.put(queue_item)
    logger.info(
        "Task enqueued for Cursor Agent processing: task_id=%s event=%s queue_size=%s",
        task_id,
        event,
        task_queue.qsize(),
    )


async def process_task_queue_worker() -> None:
    logger.info("Task processing worker started.")
    while True:
        job = await task_queue.get()
        task_id = str(job.get("task_id", ""))
        event = str(job.get("event", "unknown"))
        task_details = str(job.get("task_details", ""))
        logger.info(
            "Dequeued task for processing: task_id=%s event=%s queue_size=%s",
            task_id,
            event,
            task_queue.qsize(),
        )
        try:
            repo_info = (
                f"Target repository: {TARGET_REPO_PATH}\n"
                f"GitHub: {GITHUB_OWNER}/{GITHUB_REPO}\n"
                if TARGET_REPO_PATH else ""
            )
            prompt = (
                "You are an autonomous coding agent. A ClickUp task has been assigned to you.\n"
                "Your job is to READ the task description, IMPLEMENT the changes in code, "
                "and CREATE a pull request on GitHub when done.\n\n"
                f"{repo_info}"
                f"Event: {event}\n"
                f"Task ID: {task_id}\n\n"
                "Here are the COMPLETE task details from the ClickUp API (JSON):\n"
                f"{task_details}\n\n"
                "You already have all the task context above — do NOT try to fetch "
                "the task again via MCP or any other tool.\n\n"
                "INSTRUCTIONS — you MUST actually execute each step, not just list them:\n"
                "1. Read the task name and description carefully to understand what is asked.\n"
                f"2. Run: git checkout main && git pull origin main\n"
                f"3. Run: git checkout -b task/{task_id}\n"
                "4. Implement the required changes in the repository files.\n"
                "5. Run: git add -A && git commit -m 'task/{task_id}: <summary of changes>'\n"
                f"6. Run: git push -u origin task/{task_id}\n"
                f"7. Create a Pull Request on GitHub using the GitHub MCP to {GITHUB_OWNER}/{GITHUB_REPO}.\n"
                f"   - Base branch: main\n"
                f"   - Head branch: task/{task_id}\n"
                "   - PR title should reference the task name.\n"
                "   - PR body should describe what was done.\n"
                "8. If you created a PR, the outcome is 'review'.\n"
                "   If the task doesn't require code changes and you completed it, outcome is 'done'.\n"
                "   If you're blocked, outcome is 'blocked'.\n\n"
                "You MUST run the git and push commands yourself using the terminal. Do NOT just list them.\n\n"
                "IMPORTANT: At the very end of your response, you MUST include exactly "
                "one of these outcome lines on its own line:\n"
                "  OUTCOME: done        — if you fully completed the task (no PR needed)\n"
                "  OUTCOME: review      — if you created a PR or the work needs review\n"
                "  OUTCOME: blocked     — if you cannot proceed (missing info, access, etc.)\n"
                "  OUTCOME: in_progress — if the task is partially done and needs more work\n"
            )
            agent_result = await run_agent_prompt(prompt)
            logger.info(
                "Cursor Agent processed queued task successfully: task_id=%s result=%s",
                task_id,
                agent_result,
            )

            # Parse outcome from agent response and update ClickUp status
            outcome = _parse_agent_outcome(agent_result)
            logger.info("Parsed agent outcome: task_id=%s outcome=%s", task_id, outcome)

            if outcome in OUTCOME_STATUS_MAP:
                list_id = _extract_list_id(task_details)
                if list_id:
                    ok, result = await update_task_status(
                        task_id, list_id, OUTCOME_STATUS_MAP[outcome], outcome
                    )
                    logger.info(
                        "Status update after agent: task_id=%s outcome=%s success=%s result=%s",
                        task_id, outcome, ok, result,
                    )
                else:
                    logger.warning(
                        "Cannot update status — no list_id found: task_id=%s", task_id
                    )
            else:
                logger.info(
                    "No status change needed for outcome=%s task_id=%s", outcome, task_id
                )
        except Exception as e:
            logger.exception(
                "Cursor Agent failed while processing queued task task_id=%s error=%s",
                task_id,
                e,
            )
        finally:
            task_queue.task_done()


@asynccontextmanager
async def lifespan(app: FastAPI):
    public_url = PUBLIC_URL or "https://nonfreezing-momentarily-sharonda.ngrok-free.dev"
    webhook_endpoint = f"{public_url}/webhook"

    try:
        global webhook_credentials
        global agent_worker_task
        webhook_credentials = await register_clickup_webhook(webhook_endpoint)
        logger.info("Webhook registered: %s", webhook_credentials.id)
        agent_worker_task = asyncio.create_task(process_task_queue_worker())
    except httpx.HTTPStatusError as e:
        logger.error(
            "Failed to register ClickUp webhook: "
            f"{e.response.status_code} {e.response.text}"
        )
    except Exception as e:
        logger.exception("Failed to register ClickUp webhook: %s", e)
        agent_worker_task = asyncio.create_task(process_task_queue_worker())

    yield

    logger.info("Clickent is shutting down...")
    if webhook_credentials:
        webhook_id = webhook_credentials.id
        try:
            await delete_clickup_webhook(webhook_id)
            logger.info("Webhook %s deleted.", webhook_id)
        except httpx.HTTPStatusError as e:
            logger.error(
                f"Failed to delete webhook {webhook_id}: "
                f"{e.response.status_code} {e.response.text}"
            )
        except Exception as e:
            logger.exception("Failed to delete webhook %s: %s", webhook_id, e)
    if agent_worker_task:
        agent_worker_task.cancel()
        try:
            await agent_worker_task
        except asyncio.CancelledError:
            logger.info("Task processing worker stopped.")
    manager.disconnect_all()


app = FastAPI(
    title="clickent",
    version="1.0.0",
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[PUBLIC_URL],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/webhook")
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

    await manager.broadcast({
        "type": "clickup_webhook",
        "payload": payload,
        "task_details": task_details_for_agent,
        "moved_to_in_progress": move_status_ok,
        "move_status_result": move_status_result,
        "enqueued": True,
    })
    return {
        "status": "ok",
        "task_id": task_id,
        "moved_to_in_progress": move_status_ok,
        "move_status_result": move_status_result,
        "enqueued": True,
    }


