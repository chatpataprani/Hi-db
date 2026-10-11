import hashlib
import hmac
import os
import threading
import time
from typing import Any

import duckdb
from fastapi import FastAPI, Header, HTTPException, Query
from huggingface_hub import HfFileSystem

DEVELOPER = "chatpataprani"
BUCKET_ID = os.getenv("HI_DB_BUCKET", "Chatpataprani/HITECH_DATABASE-bucket")
INDEX_PREFIX = os.getenv("HI_DB_INDEX_PREFIX", "index/account_id")
INDEX_SHARDS = 256
MAX_RESULTS = max(1, min(int(os.getenv("MAX_RESULTS", "10")), 25))
API_KEY = os.getenv("HI_DB_API_KEY", "")
HF_TOKEN = os.getenv("HF_TOKEN", "")

OUTPUT_COLUMNS = [
    "account_id",
    "phone",
    "email",
    "linked_id",
    "linked_name",
    "linked_handle",
]

app = FastAPI(title="Hi-db Account Lookup API", version="8.0.0")
_db_lock = threading.Lock()
_connection: duckdb.DuckDBPyConnection | None = None
_fs: HfFileSystem | None = None


def _get_connection() -> duckdb.DuckDBPyConnection:
    global _connection, _fs
    if _connection is not None:
        return _connection

    with _db_lock:
        if _connection is not None:
            return _connection

        # Vercel functions have writable temporary storage only.
        os.makedirs("/tmp/duckdb_extensions", exist_ok=True)
        con = duckdb.connect()
        con.execute("SET home_directory='/tmp'")
        con.execute("SET extension_directory='/tmp/duckdb_extensions'")
        con.execute("SET threads=2")

        fs = HfFileSystem(token=HF_TOKEN if HF_TOKEN else False)
        con.register_filesystem(fs)
        _connection = con
        _fs = fs
        return con


def _shard_for(account_id: str) -> str:
    # Must match the builder's substr(md5(account_id), 1, 2) partition key.
    return hashlib.md5(account_id.encode("utf-8")).hexdigest()[:2]


def _require_api_key(authorization: str | None) -> None:
    # Fail closed: do not publish phone/email lookups accidentally if the
    # deployment secret has not been configured.
    if not API_KEY:
        raise HTTPException(
            status_code=503,
            detail="Lookup is disabled until HI_DB_API_KEY is configured in Vercel.",
        )
    expected = f"Bearer {API_KEY}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="A valid Bearer token is required.")


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "status": "online",
        "developer": DEVELOPER,
        "architecture": "Vercel + remote Hugging Face Parquet index",
        "backend_configured": True,
        "index_shards": INDEX_SHARDS,
        "search_field": "account_id",
        "authentication_configured": bool(API_KEY),
        "endpoints": {
            "lookup": "/lookup?account_id=YOUR_ACCOUNT_ID&limit=10",
            "health": "/health",
        },
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "developer": DEVELOPER,
        "storage": "Hugging Face remote Parquet index",
        "index_prefix": INDEX_PREFIX,
        "index_shards": INDEX_SHARDS,
        "authentication_configured": bool(API_KEY),
        "lookup_enabled": bool(API_KEY),
    }


@app.get("/lookup")
def lookup(
    account_id: str = Query(..., min_length=1, max_length=128),
    limit: int = Query(10, ge=1, le=25),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_api_key(authorization)
    key = account_id.strip()
    if not key:
        raise HTTPException(status_code=400, detail="account_id cannot be blank.")

    shard = _shard_for(key)
    remote_path = (
        f"hf://buckets/{BUCKET_ID}/{INDEX_PREFIX}/shard={shard}/data_0.parquet"
    )
    started = time.perf_counter()

    try:
        with _db_lock:
            con = _get_connection()
            # The shard path is assembled only from configured bucket/prefix and
            # a two-character hexadecimal shard; values remain SQL parameters.
            escaped_path = remote_path.replace("'", "''")
            sql = f"""
                SELECT {", ".join(OUTPUT_COLUMNS)}
                FROM read_parquet('{escaped_path}')
                WHERE CAST(account_id AS VARCHAR) = ?
                LIMIT ?
            """
            cursor = con.execute(sql, [key, min(limit, MAX_RESULTS)])
            columns = [item[0] for item in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    except Exception as exc:
        # Do not expose tokens, remote URLs, or raw connector errors to clients.
        message = str(exc).lower()
        if "no files found" in message or "does not exist" in message or "404" in message:
            raise HTTPException(
                status_code=503,
                detail="The remote index is missing or not built yet. Run scripts/build_remote_index.py.",
            )
        raise HTTPException(
            status_code=503,
            detail="Remote index query failed. Check the index files and Hugging Face access configuration.",
        )

    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    return {
        "success": bool(rows),
        "developer": DEVELOPER,
        "search_field": "account_id",
        "query": key,
        "count": len(rows),
        "results": rows,
        "lookup_ms": elapsed_ms,
        "index_shard": shard,
    }
