import os
import sqlite3
import time
from pathlib import Path
from typing import Iterator
from contextlib import contextmanager
from queue import Queue, Empty

from fastapi import FastAPI, HTTPException, Query

DB_PATH = Path(os.getenv("DB_PATH", "/data/telegram_lookup.db")).resolve()
POOL_SIZE = max(2, min(int(os.getenv("DB_POOL_SIZE", "8")), 32))
MAX_RESULTS = max(1, min(int(os.getenv("MAX_RESULTS", "10")), 25))
pool: Queue = Queue(maxsize=POOL_SIZE)

app = FastAPI(title="Chatpataprani Telegram Account Lookup", version="1.0.0")


def connect():
    if not DB_PATH.is_file():
        raise RuntimeError("Lookup index is not available.")
    conn = sqlite3.connect(f"{DB_PATH.as_uri()}?mode=ro", uri=True, timeout=2.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


@app.on_event("startup")
def startup():
    if DB_PATH.is_file():
        for _ in range(POOL_SIZE):
            pool.put(connect())


@app.on_event("shutdown")
def shutdown():
    while not pool.empty():
        try:
            pool.get_nowait().close()
        except Empty:
            break


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    try:
        conn = pool.get(timeout=2.0)
    except Empty:
        raise HTTPException(status_code=503, detail="Search service is busy; retry shortly.")
    try:
        yield conn
    finally:
        pool.put(conn)


@app.get("/health")
def health():
    return {
        "status": "ok" if DB_PATH.is_file() else "waiting_for_index",
        "index_ready": DB_PATH.is_file(),
        "search_field": "account_id",
        "return_fields": ["phone", "email", "linked_id", "linked_name", "linked_handle"],
    }


@app.get("/lookup")
def lookup(account_id: str = Query(..., min_length=1, max_length=128),
           limit: int = Query(10, ge=1, le=25)):
    account_id = account_id.strip()
    if not account_id:
        raise HTTPException(status_code=400, detail="account_id cannot be blank.")
    started = time.perf_counter()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT phone, email, linked_id, linked_name, linked_handle "
            "FROM records WHERE account_id = ? LIMIT ?",
            (account_id, min(limit, MAX_RESULTS)),
        ).fetchall()
    results = []
    for row in rows:
        item = {key: row[key] for key in
                ("phone", "email", "linked_id", "linked_name", "linked_handle")
                if row[key] is not None and str(row[key]).strip() and str(row[key]).casefold() != "null"}
        if item:
            results.append(item)
    return {
        "developer": "chatpataprani",
        "status": "success" if results else "not_found",
        "count": len(results),
        "lookup_ms": round((time.perf_counter() - started) * 1000, 3),
        "results": results,
    }
