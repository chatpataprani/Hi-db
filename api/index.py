import hashlib
import os
import threading
import time
from typing import Any

import duckdb
from fastapi import FastAPI, HTTPException, Query
from huggingface_hub import HfFileSystem

DEVELOPER = "chatpataprani"
BUCKET_ID = os.getenv("HI_DB_BUCKET", "Chatpataprani/HITECH_DATABASE-bucket")
INDEX_PREFIX = os.getenv("HI_DB_INDEX_PREFIX", "index/account_id")
INDEX_SHARDS = 256
MAX_RESULTS = max(1, min(int(os.getenv("MAX_RESULTS", "10")), 25))
HF_TOKEN = os.getenv("HF_TOKEN", "")

OUTPUT_COLUMNS = [
    "account_id",
    "phone",
    "email",
    "linked_id",
    "linked_name",
    "linked_handle",
]

app = FastAPI(title="Hi-db Account Lookup API", version="8.1.0")
_connection_lock = threading.Lock()
_thread_local = threading.local()


def _get_connection() -> duckdb.DuckDBPyConnection:
    # FastAPI runs synchronous endpoints in worker threads. Keep one DuckDB
    # connection per worker so separate lookups do not serialize on one handle.
    con = getattr(_thread_local, "connection", None)
    if con is not None:
        return con

    with _connection_lock:
        os.makedirs("/tmp/duckdb_extensions", exist_ok=True)
        con = duckdb.connect()
        con.execute("SET home_directory='/tmp'")
        con.execute("SET extension_directory='/tmp/duckdb_extensions'")
        con.execute("SET threads=2")
        fs = HfFileSystem(token=HF_TOKEN if HF_TOKEN else False)
        con.register_filesystem(fs)
        _thread_local.connection = con
        return con


def _shard_for(account_id: str) -> str:
    # Must match the builder's substr(md5(account_id), 1, 2) partition key.
    return hashlib.md5(account_id.encode("utf-8")).hexdigest()[:2]


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "status": "online",
        "developer": DEVELOPER,
        "architecture": "Vercel + remote Hugging Face Parquet index",
        "backend_configured": True,
        "index_shards": INDEX_SHARDS,
        "search_field": "account_id",
        "authentication_required": False,
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
        "authentication_required": False,
        "lookup_enabled": True,
    }


@app.get("/lookup")
def lookup(
    account_id: str = Query(..., min_length=1, max_length=128),
    limit: int = Query(10, ge=1, le=25),
) -> dict[str, Any]:
    key = account_id.strip()
    if not key:
        raise HTTPException(status_code=400, detail="account_id cannot be blank.")

    shard = _shard_for(key)
    remote_path = (
        f"hf://buckets/{BUCKET_ID}/{INDEX_PREFIX}/shard={shard}/data_0.parquet"
    )
    started = time.perf_counter()

    try:
        con = _get_connection()
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
        # Keep credentials and connector internals out of public error responses.
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
