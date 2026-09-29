"""Health check and service info."""

from fastapi import APIRouter

from app.__version__ import __url__, __version__
from app.api.dependencies import SOURCE_IDS

router = APIRouter(prefix="/api", tags=["System"])


@router.get("/health", summary="Health check")
def health():
    return {"status": "healthy", "version": __version__}


@router.get("/info", summary="Service info")
def info():
    return {
        "name": "TugrikRate Rates API",
        "version": __version__,
        "schema_version": 1,
        "documentation": "/",
        "upstream": __url__,
        "sources": SOURCE_IDS,
        "endpoints": {
            "/v1/rates": "Latest rates from every source",
            "/v1/sources": "Source registry and channel evidence",
            "/v1/rates/{source_id}/history": "Snapshot history",
            "/api/health": "Health check",
            "/api/admin/crawl": "Trigger a crawl (admin key required)",
        },
    }
