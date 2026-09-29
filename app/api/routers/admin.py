"""Admin endpoints: on-demand crawls.

Only needed when the in-process scheduler is disabled and something
external drives crawls over HTTP, or to force a refresh while
debugging. All routes require X-Admin-Key.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app.api.dependencies import SourceId, require_admin_key
from app.services import admin_jobs

router = APIRouter(
    prefix="/api/admin",
    tags=["Admin"],
    dependencies=[Depends(require_admin_key)],
)


@router.post("/crawl", status_code=202, summary="Crawl every source now")
def start_crawl(background_tasks: BackgroundTasks):
    """Starts a crawl of all sources in the background (202 Accepted).
    Poll GET /api/admin/status for the outcome."""
    if not admin_jobs.try_start("crawl"):
        raise HTTPException(409, "Another job is already running")
    background_tasks.add_task(admin_jobs.run_crawl_job)
    return {"status": "started", "job_type": "crawl"}


@router.post("/crawl/{source_id}", summary="Crawl one source now")
def start_single_source_crawl(source_id: SourceId):
    """Crawls and persists one source synchronously."""
    if not admin_jobs.try_start(f"crawl:{source_id.value}"):
        raise HTTPException(409, "Another job is already running")

    result = admin_jobs.run_single_source_job(source_id.value)
    if not result.get("ok"):
        raise HTTPException(
            502, f"{source_id.value} crawl failed: {result.get('error')}"
        )
    return result


@router.get("/status", summary="Last/current job state")
def get_status():
    return admin_jobs.get_status()
