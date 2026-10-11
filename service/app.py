import hmac
import os
import sqlite3
import time
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

DB_PATH = Path(os.getenv("DB_PATH", "/data/users_data.db")).resolve()
API_TOKEN = os.getenv("API_TOKEN", "")
MAX_RESULTS = max(1, min(int(os.getenv("MAX_RESULTS", "10")), 25))
QUERY_TIMEOUT_SECONDS = max(1, int(os.getenv("QUERY_TIMEOUT_SECONDS", "10")))

# Only these exact-match indexed columns are supported. Never accept a
# user-supplied SQL identifier or run a full-table scan.
FIELD_COLUMNS = {
    "mobile": "mobile",
    "id": "id",
    "email": "email",
    "alt": "alt",
}
bearer = HTTPBearer(auto_error=False)

app = FastAPI(
    title="Hi-db Search Service",
    version="1.0.0",
    description="Read-only exact-match lookups against a locally mounted SQLite database.",
)


def require_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> None:
    if not API_TOKEN:
        raise HTTPException(status_code=503, detail="Search service authentication is not configured.")
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Bearer token required.")
    if not hmac.compare_digest(credentials.credentials, API_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid API token.")


def open_database() -> sqlite3.Connection:
    if not DB_PATH.is_file():
        raise HTTPException(status_code=503, detail="Database file is not mounted on this server.")
    # Open read-only; don't let requests mutate the source database.
    conn = sqlite3.connect(
        f"{DB_PATH.as_uri()}?mode=ro",
        uri=True,
        timeout=QUERY_TIMEOUT_SECONDS,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    conn.execute(f"PRAGMA busy_timeout = {QUERY_TIMEOUT_SECONDS * 1000}")
    return conn


@app.get("/health")
def health():
    # Do not expose the filesystem path, token, or database rows.
    return {
        "status": "ok" if DB_PATH.is_file() else "waiting_for_database",
        "database_mounted": DB_PATH.is_file(),
        "auth_configured": bool(API_TOKEN),
        "supported_fields": list(FIELD_COLUMNS),
        "max_results": MAX_RESULTS,
    }


@app.get("/lookup", dependencies=[Depends(require_token)])
def lookup(
    field: Literal["mobile", "id", "email", "alt"] = Query(...),
    value: str = Query(..., min_length=1, max_length=320),
    limit: int = Query(10, ge=1, le=25),
):
    value = value.strip()
    if not value:
        raise HTTPException(status_code=400, detail="value cannot be blank.")
    if field == "mobile" and not value.isdigit():
        raise HTTPException(status_code=400, detail="mobile must contain digits only.")
    if field == "id" and len(value) > 128:
        raise HTTPException(status_code=400, detail="id is too long.")

    column = FIELD_COLUMNS[field]  # fixed allowlist, not user-provided SQL
    started = time.perf_counter()
    conn = open_database()
    try:
        # These fields have existing indexes in the source database.
        rows = conn.execute(
            f'SELECT mobile, name, fname, address, alt, circle, id, email '
            f'FROM users WHERE "{column}" = ? LIMIT ?',
            (value, min(limit, MAX_RESULTS)),
        ).fetchall()
        results = [dict(row) for row in rows]
    except sqlite3.Error as exc:
        # Do not leak SQL or file paths in the public response.
        raise HTTPException(status_code=503, detail="SQLite query failed.") from exc
    finally:
        conn.close()

    return {
        "status": "success" if results else "not_found",
        "field": field,
        "count": len(results),
        "lookup_ms": round((time.perf_counter() - started) * 1000, 3),
        "results": results,
    }
