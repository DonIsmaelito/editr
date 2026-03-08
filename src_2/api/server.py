"""
Editr SSE Server

FastAPI app with POST /api/edit endpoint.
Streams progress as Server-Sent Events (same pattern as src/api/server.py).
Also supports a fire-and-forget /edit endpoint that returns a job_id
for Convex subscription-based progress.
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src_2.config import CONVEX_URL, GOOGLE_CLOUD_API_KEY
from src_2.db import convex_store
from src_2.pipeline import run_pipeline

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("editr.api")


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 60)
    logger.info("Editr SSE Server starting up")
    logger.info(f"  Google Cloud API key configured: {bool(GOOGLE_CLOUD_API_KEY)}")
    logger.info(f"  Convex URL configured:           {bool(CONVEX_URL)}")
    logger.info("=" * 60)
    yield
    logger.info("Editr SSE Server shutting down")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Editr API",
    description="AI video re-editing for virality — SSE streaming",
    version="0.1.0",
    lifespan=lifespan,
)

ROOT_DIR = Path(__file__).resolve().parents[2]

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount(
    "/media/downloads",
    StaticFiles(directory=str(ROOT_DIR / "downloads"), check_dir=False),
    name="editr-downloads",
)
app.mount(
    "/media/outputs",
    StaticFiles(directory=str(ROOT_DIR / "outputs"), check_dir=False),
    name="editr-outputs",
)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------
class EditRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=200)
    platform: str = Field(default="tiktok")
    max_videos: int = Field(default=3, ge=1, le=10)


class EditStartResponse(BaseModel):
    job_id: str
    status: str


_running_tasks: dict[str, asyncio.Task] = {}


# ---------------------------------------------------------------------------
# SSE endpoint (streaming progress)
# ---------------------------------------------------------------------------
async def _edit_sse_impl(req: EditRequest):
    queue: asyncio.Queue = asyncio.Queue()

    async def on_progress(event_type: str, data):
        await queue.put((event_type, data))

    async def run_and_signal_done():
        try:
            await run_pipeline(
                username=req.username,
                platform=req.platform,
                max_videos=req.max_videos,
                on_progress=on_progress,
            )
        except Exception as e:
            logger.error(f"[SSE] Pipeline exception: {e}", exc_info=True)
            await queue.put(("error", {"message": str(e)}))
        finally:
            await queue.put(None)

    async def event_generator():
        task = asyncio.create_task(run_and_signal_done())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                event_type, data = item
                payload = json.dumps(data, default=str)
                yield f"event: {event_type}\ndata: {payload}\n\n"
        except asyncio.CancelledError:
            task.cancel()
            raise

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/edit/stream")
async def edit_sse(req: EditRequest):
    return await _edit_sse_impl(req)


# ---------------------------------------------------------------------------
# Fire-and-forget endpoint (Convex subscription-based)
# ---------------------------------------------------------------------------
async def _run_background_edit(job_id: str, req: EditRequest):
    try:
        await run_pipeline(
            username=req.username,
            platform=req.platform,
            max_videos=req.max_videos,
            job_id=job_id,
        )
    except Exception as e:
        logger.error(f"[edit:{job_id}] Background pipeline failed: {e}", exc_info=True)
    finally:
        _running_tasks.pop(job_id, None)


@app.post("/api/edit", response_model=EditStartResponse)
async def start_edit(req: EditRequest):
    try:
        job_id = str(convex_store.create_job(
            username=req.username,
            platform=req.platform,
            max_videos=req.max_videos,
        ))
    except Exception as e:
        logger.error(f"[edit] Convex create_job failed: {e}")
        raise HTTPException(
            status_code=503,
            detail="Convex is unavailable; cannot start edit job.",
        )

    task = asyncio.create_task(_run_background_edit(job_id, req))
    _running_tasks[job_id] = task
    return EditStartResponse(job_id=job_id, status="scraping")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "service": "editr",
        "version": "0.1.0",
        "google_cloud_configured": bool(GOOGLE_CLOUD_API_KEY),
        "convex_configured": bool(CONVEX_URL),
    }
