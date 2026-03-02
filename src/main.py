import asyncio
import logging
from logging import getLogger
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import src.services.config as config
from src.services.config import PUBLIC_URL, LOG_LEVEL
from src.services.clickup import register_clickup_webhook, delete_clickup_webhook
from src.services.agent import process_task_queue_worker
from src.routes.webhook.webhook import router as webhook_router

logger = getLogger(name=__name__)


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    public_url = PUBLIC_URL or "https://nonfreezing-momentarily-sharonda.ngrok-free.dev"
    webhook_endpoint = f"{public_url}/webhook"

    try:
        config.webhook_credentials = await register_clickup_webhook(webhook_endpoint)
        logger.info("Webhook registered: %s", config.webhook_credentials.id)
        config.agent_worker_task = asyncio.create_task(process_task_queue_worker())
    except httpx.HTTPStatusError as e:
        logger.error(
            "Failed to register ClickUp webhook: "
            f"{e.response.status_code} {e.response.text}"
        )
    except Exception as e:
        logger.exception("Failed to register ClickUp webhook: %s", e)
        config.agent_worker_task = asyncio.create_task(process_task_queue_worker())

    yield

    logger.info("Clickent is shutting down...")
    if config.webhook_credentials:
        webhook_id = config.webhook_credentials.id
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
    if config.agent_worker_task:
        config.agent_worker_task.cancel()
        try:
            await config.agent_worker_task
        except asyncio.CancelledError:
            logger.info("Task processing worker stopped.")


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


app.include_router(webhook_router)
