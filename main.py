"""FastAPI app for the notification batcher."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from db import Database
from service import NotificationService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("notification_batcher")

DB_PATH = os.environ.get("BATCHER_DB", "batcher.db")
LOG_PATH = os.environ.get("NOTIFICATIONS_LOG", "notifications.log")
WINDOW_SECONDS = float(os.environ.get("WINDOW_SECONDS", "60"))
THRESHOLD = int(os.environ.get("THRESHOLD", "10"))
FLUSH_SECONDS = float(os.environ.get("FLUSH_SECONDS", "30"))

db = Database(DB_PATH)
service = NotificationService(
    db,
    log_path=LOG_PATH,
    window_seconds=WINDOW_SECONDS,
    threshold=THRESHOLD,
    flush_seconds=FLUSH_SECONDS,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "Batcher up: window=%ss threshold=%s/min flush=%ss db=%s log=%s",
        WINDOW_SECONDS, THRESHOLD, FLUSH_SECONDS, DB_PATH, LOG_PATH,
    )
    yield
    service.shutdown()


app = FastAPI(title="Notification Batcher", lifespan=lifespan)


class LikeEvent(BaseModel):
    postId: str = Field(..., min_length=1)
    likerName: str = Field(..., min_length=1)
    authorId: str = Field(..., min_length=1)


@app.post("/events", status_code=201)
async def post_event(evt: LikeEvent):
    return await run_in_threadpool(
        service.record_like, evt.postId, evt.likerName, evt.authorId
    )


@app.get("/notifications")
async def get_notifications(authorId: str = Query(..., min_length=1)):
    items = await run_in_threadpool(service.list_notifications, authorId)
    return {"authorId": authorId, "notifications": items}
