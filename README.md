# Clickent

Clickent is a FastAPI-based webhook server that bridges ClickUp tasks with an AI coding agent (like Claude or Cursor). When a task is assigned to you in ClickUp with the "agent" tag, Clickent automatically triggers an AI agent to implement the task in your GitHub repository and create a pull request.

## How It Works

1. **Webhook Registration**: On startup, Clickent registers a webhook with ClickUp to listen for `taskCreated` and `taskUpdated` events.

2. **Task Eligibility**: When a webhook is received, Clickent checks if the task is eligible for agent processing:
   - Must have the "agent" tag
   - Must be assigned to the configured user
   - Must be in "open" or "in progress" status

3. **Task Processing**: For eligible tasks:
   - Moves the task to "in progress" status
   - Enqueues the task for the AI agent
   - The agent reads the task, implements changes, creates a branch, commits code, and opens a PR

4. **Status Updates**: Based on the agent's outcome (detected from output), Clickent updates the task status to "done", "review", or "blocked".

## Requirements

- Python 3.14+
- ClickUp account with API access
- GitHub repository
- AI agent CLI (e.g., `agent` command from Claude/Cursor)

## Installation

```bash
# Clone the repository
git clone <repository-url>
cd clickent

# Install dependencies
uv sync
```

## Configuration

Create a `.env` file with the following variables:

```env
# ClickUp Configuration
CLICKUP_ACCESS_TOKEN=your_clickup_api_token
CLICKUP_WORKSPACE_ID=your_workspace_id
CLICKUP_ASSIGNEE_NAME=your_username
CLICKUP_ASSIGNEE_ID=your_user_id

# Public URL for webhooks (optional, uses ngrok if not set)
PUBLIC_URL=https://your-public-url.com

# Agent Configuration
AGENT_COMMAND=agent
AGENT_MODEL=
AGENT_TIMEOUT_SECONDS=120
AGENT_CLICKUP_MCP_IDENTIFIER=clickup

# Repository Configuration
TARGET_REPO_PATH=/path/to/your/repo
GITHUB_OWNER=your_github_username
GITHUB_REPO=your_repository_name

# Optional
LOG_LEVEL=INFO
```

### Getting ClickUp Credentials

1. **Access Token**: Get your API token from ClickUp's [Integrations](https://app.clickup.com/settings/integrations) page
2. **Workspace ID**: Found in the URL when viewing your workspace (e.g., `app.clickup.com/12345678`)
3. **User ID**: Found in your ClickUp profile settings

## Running the Server

```bash
# Development
uv run python -m src.main

# With ngrok for public access
ngrok http 8000
```

The server runs on port 8000 by default.

## API Endpoints

- `GET /health` - Health check endpoint
- `POST /webhook` - ClickUp webhook endpoint

## Architecture

```
clickent/
├── src/
│   ├── main.py              # FastAPI app and lifespan
│   ├── routes/
│   │   └── webhook/
│   │       └── webhook.py   # Webhook handler
│   ├── services/
│   │   ├── agent.py         # Agent execution and task queue
│   │   ├── clickup.py      # ClickUp API integration
│   │   └── config.py       # Configuration management
│   └── models/
│       ├── task.py
│       ├── webhook.py
│       └── events.py
```

## License

MIT
