"""Centralised configuration loaded from environment variables."""

import os
import asyncio
from typing import Any

from dotenv import load_dotenv

from src.models.webhook import WebhookRegistrationResponse

load_dotenv()

# ClickUp
CLICKUP_ACCESS_TOKEN = os.getenv("CLICKUP_ACCESS_TOKEN", "")
CLICKUP_WORKSPACE_ID = os.getenv("CLICKUP_WORKSPACE_ID", "")
CLICKUP_ASSIGNEE_NAME = os.getenv("CLICKUP_ASSIGNEE_NAME", "")
CLICKUP_ASSIGNEE_ID = os.getenv("CLICKUP_ASSIGNEE_ID", "")

# Public
PUBLIC_URL = os.getenv("PUBLIC_URL", "")

# Agent
AGENT_COMMAND = os.getenv("AGENT_COMMAND", "agent")
AGENT_MODEL = os.getenv("AGENT_MODEL", "")
AGENT_TIMEOUT_SECONDS = int(os.getenv("AGENT_TIMEOUT_SECONDS", "120"))
AGENT_CLICKUP_MCP_IDENTIFIER = os.getenv("AGENT_CLICKUP_MCP_IDENTIFIER", "clickup")
AGENT_QUEUE_MAXSIZE = int(os.getenv("AGENT_QUEUE_MAXSIZE", "200"))

# Repository
TARGET_REPO_PATH = os.getenv("TARGET_REPO_PATH", "")
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# ---------------------------------------------------------------------------
# Shared mutable state (modified at runtime by lifespan / workers)
# ---------------------------------------------------------------------------


webhook_credentials: WebhookRegistrationResponse | None = None
task_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=AGENT_QUEUE_MAXSIZE)
agent_worker_task: asyncio.Task | None = None
