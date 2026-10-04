import hashlib
import os
import time
from functools import lru_cache

import httpx
from fastapi import FastAPI, HTTPException

app = FastAPI(title="Hi-db Fast Lookup API", version="1.0.0")

INDEX_BASE_URL = os.getenv("INDEX_BASE_URL", "").rstrip("/")
INDEX_TOKEN = os.getenv("INDEX_TOKEN", "")
SHARD_COUNT = int(os.getenv("SHARD_COUNT", "256"))
MAX_RESULTS = int(os.getenv("MAX_RESULTS", "25"))

client = httpx.Client(
    timeout=httpx.Timeout(2.0, connect=1.0),
    limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
)

def shard(key: str) -> int:
    digest = hashlib.blake2b(key.encode(), digest_size=4).digest()
    return int.from_bytes(digest, "big") % SHARD_COUNT

@lru_cache(maxsize=4096)
def lookup(key: str):
    if not INDEX_BASE_URL:
        raise RuntimeError("INDEX_BASE_URL is not configured")
    url = f"{INDEX_BASE_URL}/records-{shard(key):03d}.json"
    headers = {"Authorization": f"Bearer {INDEX_TOKEN}"} if INDEX_TOKEN else {}
    response = client.get(url, headers=headers)
    if response.status_code == 404:
        return []
    response.raise_for_status()
    payload = response.json()
    return payload.get(key, []) if isinstance(payload, dict) else []

@app.get("/")
def root():
    return {"name":"Hi-db Fast Lookup API","status":"online","endpoints":["/health","/record={record_id}"]}

@app.get("/health")
def health():
    return {"status":"ok","index_configured":bool(INDEX_BASE_URL),"shards":SHARD_COUNT,"cache":lookup.cache_info()._asdict()}

@app.get("/record={record_id}")
def record_lookup(record_id: str):
    if not record_id or len(record_id) > 128:
        raise HTTPException(400, "Invalid record id")
    started = time.perf_counter()
    try:
        rows = lookup(record_id)[:MAX_RESULTS]
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Index request failed") from exc
    return {"status":"success","type":"record","results":rows,"count":len(rows),"lookup_ms":round((time.perf_counter()-started)*1000, 3)}
