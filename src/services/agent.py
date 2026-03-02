"""Agent execution, outcome parsing, task queue processing."""

import os
import re
import json
import asyncio
import logging

from src.services.config import (
    AGENT_CLICKUP_MCP_IDENTIFIER,
    AGENT_COMMAND,
    AGENT_MODEL,
    AGENT_TIMEOUT_SECONDS,
    GITHUB_OWNER,
    GITHUB_REPO,
    TARGET_REPO_PATH,
    task_queue,
)
from src.services.clickup import OUTCOME_STATUS_MAP, update_task_status

logger = logging.getLogger(__name__)

VALID_OUTCOMES = {"done", "review", "blocked", "in_progress"}


# ---------------------------------------------------------------------------
# Outcome parsing
# ---------------------------------------------------------------------------
def parse_agent_outcome(agent_output: str) -> str:
    for line in reversed(agent_output.splitlines()):
        match = re.match(r"^\s*OUTCOME:\s*(\w+)", line, re.IGNORECASE)
        if match:
            outcome = match.group(1).strip().lower()
            if outcome in VALID_OUTCOMES:
                return outcome

    tail = agent_output[-500:].lower()
    if "pull request" in tail or "pr" in agent_output[-500:]:
        return "review"
    if "completed" in tail or "task is done" in tail:
        return "done"
    if "blocked" in tail or "cannot proceed" in tail:
        return "blocked"
    return "in_progress"


def extract_list_id(task_details_json: str) -> str | None:
    """Pull the list ID from a task-details JSON string."""
    try:
        data = json.loads(task_details_json)
        return str(data.get("list", {}).get("id", "")) or None
    except (json.JSONDecodeError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Agent subprocess
# ---------------------------------------------------------------------------
async def run_agent_prompt(prompt: str) -> str:
    """Run the CLI agent and return its stdout."""
    command = [
        AGENT_COMMAND,
        "--print",
        "--output-format", "text",
        "--force",
        "--approve-mcps",
        "--workspace", TARGET_REPO_PATH or os.getcwd(),
    ]
    if AGENT_MODEL:
        command.extend(["--model", AGENT_MODEL])
    command.append(prompt)

    logger.info(
        "Running agent: model=%s timeout=%ss",
        AGENT_MODEL or "default", AGENT_TIMEOUT_SECONDS,
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
            process.communicate(), timeout=AGENT_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as e:
        process.kill()
        await process.wait()
        logger.error("Agent timed out after %ss", AGENT_TIMEOUT_SECONDS)
        raise RuntimeError(f"Agent timed out after {AGENT_TIMEOUT_SECONDS}s.") from e

    if process.returncode != 0:
        logger.error("Agent failed: return_code=%s", process.returncode)
        raise RuntimeError(
            "Agent execution failed. "
            f"stderr={stderr.decode('utf-8', errors='ignore').strip()}"
        )

    output = stdout.decode("utf-8", errors="ignore").strip()
    if not output:
        raise RuntimeError("Agent returned empty output.")
    logger.info("Agent completed successfully.")
    return output


async def get_clickup_task_details_via_agent(task_id: str) -> str:
    prompt = (
        "Use the configured ClickUp MCP server "
        f"('{AGENT_CLICKUP_MCP_IDENTIFIER}') to fetch the full task details for "
        f"task ID '{task_id}'. Return only a concise JSON object with keys: "
        "id, name, description, status, assignees, url."
    )
    return await run_agent_prompt(prompt)


# ---------------------------------------------------------------------------
# Task queue helpers
# ---------------------------------------------------------------------------
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
        "Task enqueued: task_id=%s event=%s queue_size=%s",
        task_id, event, task_queue.qsize(),
    )


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------
def _build_agent_prompt(task_id: str, event: str, task_details: str) -> str:
    """Build the full prompt sent to the coding agent."""
    repo_info = (
        f"Target repository: {TARGET_REPO_PATH}\n"
        f"GitHub: {GITHUB_OWNER}/{GITHUB_REPO}\n"
        if TARGET_REPO_PATH else ""
    )
    return (
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
        f"5. Run: git add -A && git commit -m 'task/{task_id}: <summary of changes>'\n"
        f"6. Run: git push -u origin task/{task_id}\n"
        f"7. Create a Pull Request on GitHub using the GitHub MCP to {GITHUB_OWNER}/{GITHUB_REPO}.\n"
        f"   - Base branch: main\n"
        f"   - Head branch: task/{task_id}\n"
        "   - PR title should reference the task name.\n"
        "   - PR body should describe what was done.\n"
        "8. If you created a PR, the outcome is 'review'.\n"
        "   If the task doesn't require code changes and you completed it, outcome is 'done'.\n"
        "   If you're blocked, outcome is 'blocked'.\n\n"
        "You MUST run the git and push commands yourself using the terminal. "
        "Do NOT just list them.\n\n"
        "You MUST use the GitHub MCP to create the pull request.\n"
#        "You MUST use the ClickUp MCP to update the task status.\n"
        "IMPORTANT: At the very end of your response, you MUST include exactly "
        "one of these outcome lines on its own line:\n"
        "  OUTCOME: done        — if you fully completed the task (no PR needed)\n"
        "  OUTCOME: review      — if you created a PR or the work needs review\n"
        "  OUTCOME: blocked     — if you cannot proceed (missing info, access, etc.)\n"
        "  OUTCOME: in_progress — if the task is partially done and needs more work\n"
    )


async def process_task_queue_worker() -> None:
    """Long-running worker that pulls tasks from the queue and runs the agent."""
    logger.info("Task queue worker started.")
    while True:
        job = await task_queue.get()
        task_id = str(job.get("task_id", ""))
        event = str(job.get("event", "unknown"))
        task_details = str(job.get("task_details", ""))

        logger.info(
            "Dequeued task: task_id=%s event=%s queue_size=%s",
            task_id, event, task_queue.qsize(),
        )

        try:
            prompt = _build_agent_prompt(task_id, event, task_details)
            agent_result = await run_agent_prompt(prompt)
            logger.info("Agent finished task_id=%s", task_id)

            outcome = parse_agent_outcome(agent_result)
            logger.info("Parsed outcome: task_id=%s outcome=%s", task_id, outcome)

            if outcome in OUTCOME_STATUS_MAP:
                list_id = extract_list_id(task_details)
                if list_id:
                    ok, result = await update_task_status(
                        task_id, list_id, OUTCOME_STATUS_MAP[outcome], outcome,
                    )
                    
                    logger.info(
                        "Status update: task_id=%s outcome=%s ok=%s result=%s",
                        task_id, outcome, ok, result,
                    )
                else:
                    logger.warning("No list_id found for task_id=%s", task_id)
            else:
                logger.info("No status change for outcome=%s task_id=%s", outcome, task_id)
        except Exception as e:
            logger.exception("Agent failed for task_id=%s: %s", task_id, e)
        finally:
            task_queue.task_done()