"""services package — re-exports for backward compatibility."""

# Config / env vars / shared state
from src.services.config import (  # noqa: F401
    AGENT_CLICKUP_MCP_IDENTIFIER,
    AGENT_COMMAND,
    AGENT_MODEL,
    AGENT_QUEUE_MAXSIZE,
    AGENT_TIMEOUT_SECONDS,
    CLICKUP_ACCESS_TOKEN,
    CLICKUP_ASSIGNEE_ID,
    CLICKUP_ASSIGNEE_NAME,
    CLICKUP_WORKSPACE_ID,
    GITHUB_OWNER,
    GITHUB_REPO,
    LOG_LEVEL,
    PUBLIC_URL,
    TARGET_REPO_PATH,
    agent_worker_task,
    task_queue,
    webhook_credentials,
)

# ClickUp API operations
from src.services.clickup import (  # noqa: F401
    OUTCOME_STATUS_MAP,
    delete_clickup_webhook,
    get_clickup_task_details,
    get_existing_clickup_webhook,
    is_eligible_task,
    is_status_transition_to_in_progress,
    is_task_open,
    is_task_open_or_in_progress,
    move_task_to_in_progress,
    register_clickup_webhook,
    update_task_status,
)

# Agent execution & queue
from src.services.agent import (  # noqa: F401
    enqueue_task_for_agent,
    get_clickup_task_details_via_agent,
    process_task_queue_worker,
    run_agent_prompt,
)
