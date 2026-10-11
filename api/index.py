import hmac
import os
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

DEVELOPER = "chatpataprani"
SERVICE_URL = os.getenv("SEARCH_SERVICE_URL", "").rstrip("/")
SERVICE_TOKEN = os.getenv("SEARCH_API_TOKEN", "")
CLIENT_API_KEY = os.getenv("CLIENT_API_KEY", "")
MAX_RESULTS = max(1, min(int(os.getenv("MAX_RESULTS", "10")), 25))
bearer = HTTPBearer(auto_error=False)

app = FastAPI(title="Hi-db Search API", version="6.0.0")


def require_client_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> None:
    # Fail closed until the owner configures a client key in Vercel.
    if not CLIENT_API_KEY:
        raise HTTPException(status_code=503, detail="API authentication is not configured.")
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Use Authorization: Bearer <CLIENT_API_KEY>.")
    if not hmac.compare_digest(credentials.credentials, CLIENT_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid API key.")


def require_service_config() -> None:
    if not SERVICE_URL or not SERVICE_TOKEN:
        raise HTTPException(status_code=503, detail="Search backend is not configured.")


@app.get("/")
def root():
    return {
        "status": "online",
        "developer": DEVELOPER,
        "backend_configured": bool(SERVICE_URL and SERVICE_TOKEN),
        "authentication_configured": bool(CLIENT_API_KEY),
        "endpoints": {
            "lookup": "/lookup?field=mobile&value=...",
            "health": "/health",
        },
        "note": "Exact-match lookups only; API access requires a bearer key.",
    }


@app.get("/health")
async def health():
    if not SERVICE_URL or not SERVICE_TOKEN:
        return {"status": "waiting_for_backend"}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{SERVICE_URL}/health")
        response.raise_for_status()
        data = response.json()
        return {"status": "ok", "search_service": data}
    except (httpx.HTTPError, ValueError):
        raise HTTPException(status_code=503, detail="Search backend is unreachable.")


async def proxy_lookup(field: str, value: str, limit: int) -> dict:
    require_service_config()
    if not value.strip():
        raise HTTPException(status_code=400, detail="value cannot be blank.")
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"{SERVICE_URL}/lookup",
                params={"field": field, "value": value.strip(), "limit": min(limit, MAX_RESULTS)},
                headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            )
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="Search backend request failed.")
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", "Search backend error.")
        except ValueError:
            detail = "Search backend error."
        raise HTTPException(status_code=response.status_code, detail=detail)
    try:
        return response.json()
    except ValueError:
        raise HTTPException(status_code=502, detail="Search backend returned invalid JSON.")


@app.get("/lookup", dependencies=[Depends(require_client_key)])
async def lookup(
    field: Literal["mobile", "id", "email", "alt"] = Query(...),
    value: str = Query(..., min_length=1, max_length=320),
    limit: int = Query(10, ge=1, le=25),
):
    data = await proxy_lookup(field, value, limit)
    return {"developer": DEVELOPER, **data}


@app.get("/number={value}", dependencies=[Depends(require_client_key)])
async def mobile_lookup(value: str):
    if not value.isdigit() or not 10 <= len(value) <= 15:
        raise HTTPException(status_code=400, detail="mobile must contain 10 to 15 digits.")
    data = await proxy_lookup("mobile", value, MAX_RESULTS)
    return {"developer": DEVELOPER, "type": "mobile", "value": value, **data}


@app.get("/id={value}", dependencies=[Depends(require_client_key)])
async def id_lookup(value: str):
    data = await proxy_lookup("id", value, MAX_RESULTS)
    return {"developer": DEVELOPER, "type": "id", "value": value, **data}


@app.get("/email={value}", dependencies=[Depends(require_client_key)])
async def email_lookup(value: str):
    data = await proxy_lookup("email", value, MAX_RESULTS)
    return {"developer": DEVELOPER, "type": "email", "value": value, **data}


@app.get("/alt={value}", dependencies=[Depends(require_client_key)])
async def alternate_lookup(value: str):
    if not value.isdigit():
        raise HTTPException(status_code=400, detail="alt must contain digits only.")
    data = await proxy_lookup("alt", value, MAX_RESULTS)
    return {"developer": DEVELOPER, "type": "alt", "value": value, **data}
