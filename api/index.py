import os

import httpx
from fastapi import FastAPI, HTTPException, Query

DEVELOPER = "chatpataprani"
SERVICE_URL = os.getenv("SEARCH_SERVICE_URL", "").rstrip("/")
MAX_RESULTS = max(1, min(int(os.getenv("MAX_RESULTS", "10")), 25))

app = FastAPI(title="Hi-db Account Lookup API", version="7.0.0")


@app.get("/")
def root():
    return {
        "status": "online",
        "developer": DEVELOPER,
        "backend_configured": bool(SERVICE_URL),
        "authentication_configured": False,
        "search_field": "account_id",
        "endpoints": {
            "lookup": "/lookup?account_id=YOUR_ACCOUNT_ID&limit=10",
            "health": "/health",
        },
        "note": (
            "Exact-match account_id lookup. The persistent search backend must be configured "
            "before lookups can return data."
        ),
    }


@app.get("/health")
async def health():
    if not SERVICE_URL:
        return {
            "status": "waiting_for_backend",
            "backend_configured": False,
            "search_field": "account_id",
        }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{SERVICE_URL}/health")
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(status_code=503, detail="Search backend is unreachable.")
    return {
        "status": "ok" if data.get("index_ready", data.get("status") == "ok") else "waiting_for_index",
        "backend_configured": True,
        "search_service": data,
    }


@app.get("/lookup")
async def lookup(
    account_id: str = Query(..., min_length=1, max_length=128),
    limit: int = Query(10, ge=1, le=25),
):
    account_id = account_id.strip()
    if not account_id:
        raise HTTPException(status_code=400, detail="account_id cannot be blank.")
    if not SERVICE_URL:
        raise HTTPException(
            status_code=503,
            detail="Search backend is not configured. Set SEARCH_SERVICE_URL to the persistent lookup service URL.",
        )

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"{SERVICE_URL}/lookup",
                params={"account_id": account_id, "limit": min(limit, MAX_RESULTS)},
            )
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="Search backend request failed.")

    if response.status_code >= 400:
        try:
            payload = response.json()
            detail = payload.get("detail", "Search backend error.") if isinstance(payload, dict) else "Search backend error."
        except ValueError:
            detail = "Search backend error."
        raise HTTPException(status_code=response.status_code, detail=detail)

    try:
        data = response.json()
    except ValueError:
        raise HTTPException(status_code=502, detail="Search backend returned invalid JSON.")

    return {"developer": DEVELOPER, **data}
