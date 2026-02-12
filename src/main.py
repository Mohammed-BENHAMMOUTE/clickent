import os

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from contextlib import asynccontextmanager

from src.controllers.ws import ConnectionManager

load_dotenv()

CLICKUP_ACCESS_TOKEN = os.getenv("CLICKUP_ACCESS_TOKEN", "")
CLICKUP_WORKSPACE_ID = os.getenv("CLICKUP_WORKSPACE_ID", "")
CLICKUP_WEBHOOK_ENDPOINT = os.getenv("CLICKUP_WEBHOOK_ENDPOINT", "")

manager = ConnectionManager()
webhook_credentials: dict = {}


async def register_clickup_webhook() -> dict:
    url = f"https://api.clickup.com/api/v2/team/{CLICKUP_WORKSPACE_ID}/webhook"
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            json={
                "endpoint": CLICKUP_WEBHOOK_ENDPOINT,
                "events": ["taskCreated"],
                "status": "active",
            },
            headers={"Authorization": CLICKUP_ACCESS_TOKEN},
        )
        response.raise_for_status()
        return response.json()


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Clickent is starting up...")
    try:
        data = await register_clickup_webhook()
        webhook_credentials.update(data)
        print(f"Webhook registered: {webhook_credentials.get('id', 'unknown')}")
    except Exception as e:
        print(f"Failed to register ClickUp webhook: {e}")
    yield
    print("Clickent is shutting down...")
    manager.disconnect_all()


app = FastAPI(
    title="clickent",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    await manager.connect(client_id, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            message = data.get("message", "")

            # TODO: plug in your agent logic here
            response = {"client_id": client_id, "reply": f"echo: {message}"}

            await manager.send_json(client_id, response)
    except WebSocketDisconnect:
        manager.disconnect(client_id)


