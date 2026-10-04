import os
import re
import time
import hashlib
from functools import lru_cache
from typing import Any

import pyarrow as pa
import pyarrow.dataset as ds
from fastapi import FastAPI, HTTPException
from huggingface_hub import HfFileSystem

BUCKET = "buckets/Chatpataprani/HITECH_DATABASE-bucket"
HF_TOKEN = os.getenv("HF_TOKEN") or None
MAX_RESULTS = int(os.getenv("MAX_RESULTS", "25"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "4096"))
DEVELOPER = "chatpataprani"

PHONE_RE = re.compile(r"^\d{10,15}$")
AADHAAR_RE = re.compile(r"^\d{12}$")

app = FastAPI(title="Hi-db HITECH Fast Lookup API", version="5.0")

fs = HfFileSystem(token=HF_TOKEN)

# Only compact indexes are queried at request time. The raw ~75 GB database
# is never scanned per request.
PHONE_FILES = sorted(fs.glob(f"{BUCKET}/indexes/idx_phone.*.parquet"))
AADHAAR_FILES = sorted(fs.glob(f"{BUCKET}/indexes/idx_aadhaar.*.parquet"))
if not AADHAAR_FILES:
    AADHAAR_FILES = sorted(fs.glob(f"{BUCKET}/indexes/idx_aadhar.*.parquet"))

SHARDS = 256
PHONE_SHARDS = {i: [] for i in range(SHARDS)}
AADHAAR_SHARDS = {i: [] for i in range(SHARDS)}

def _shard(value: str) -> int:
    return int.from_bytes(hashlib.blake2b(value.encode(), digest_size=4).digest(), "big") % SHARDS

for path in PHONE_FILES:
    name = path.rsplit("/", 1)[-1]
    try:
        PHONE_SHARDS[int(name.split(".")[1])].append(path)
    except (IndexError, ValueError):
        pass

for path in AADHAAR_FILES:
    name = path.rsplit("/", 1)[-1]
    try:
        AADHAAR_SHARDS[int(name.split(".")[1])].append(path)
    except (IndexError, ValueError):
        pass

INDEX_READY = any(PHONE_SHARDS.values()) and any(AADHAAR_SHARDS.values())

@lru_cache(maxsize=512)
def _dataset(kind: str, shard: int):
    files = PHONE_SHARDS[shard] if kind == "phone" else AADHAAR_SHARDS[shard]
    if not files:
        return None
    return ds.dataset(files, filesystem=fs, format="parquet")

def _column_type(dataset: ds.Dataset, column: str) -> pa.DataType:
    try:
        return dataset.schema.field(column).type
    except KeyError as exc:
        raise RuntimeError(f"Required index column '{column}' was not found. Available columns: {dataset.schema.names}") from exc

def _typed_value(dataset: ds.Dataset, column: str, value: str) -> Any:
    dtype = _column_type(dataset, column)
    if pa.types.is_integer(dtype):
        try:
            return int(value)
        except ValueError as exc:
            raise HTTPException(400, f"{column} is numeric but the supplied value is invalid.") from exc
    if pa.types.is_floating(dtype):
        try:
            return float(value)
        except ValueError as exc:
            raise HTTPException(400, f"{column} is numeric but the supplied value is invalid.") from exc
    return value

def _lookup(kind: str, column: str, value: str) -> dict[str, Any]:
    dataset = _dataset(kind, _shard(value))
    if dataset is None:
        raise HTTPException(503, "Index is not built yet. Run build_hitech_index.py once.")
    started = time.perf_counter()
    typed = _typed_value(dataset, column, value)
    scanner = dataset.scanner(filter=ds.field(column) == typed, batch_size=BATCH_SIZE, use_threads=True)
    rows: list[dict[str, Any]] = []
    for batch in scanner.to_batches():
        remaining = MAX_RESULTS - len(rows)
        if remaining <= 0:
            break
        rows.extend(batch.to_pylist()[:remaining])
        if len(rows) >= MAX_RESULTS:
            break
    return {"results": rows, "count": len(rows), "lookup_ms": round((time.perf_counter() - started) * 1000, 3)}

@app.get("/")
def root():
    return {
        "status": "online",
        "developer": DEVELOPER,
        "database": "Chatpataprani/HITECH_DATABASE-bucket",
        "database_size": "~75 GB",
        "backend": "PyArrow + HuggingFace HfFileSystem",
        "index_ready": INDEX_READY,
        "phone_index_parts": len(PHONE_FILES),
        "aadhar_index_parts": len(AADHAAR_FILES),
        "endpoints": {"number": "/number=<10-15 digit number>", "aadhar": "/aadhar=<12 digit Aadhaar>", "health": "/health"},
    }

@app.get("/health")
def health():
    return {
        "status": "ok",
        "developer": DEVELOPER,
        "database": "Chatpataprani/HITECH_DATABASE-bucket",
        "database_size": "~75 GB",
        "index_ready": INDEX_READY,
        "phone_index_parts": len(PHONE_FILES),
        "aadhar_index_parts": len(AADHAAR_FILES),
    }

@app.get("/number={number}")
def number_lookup(number: str):
    number = number.strip()
    if not PHONE_RE.fullmatch(number):
        raise HTTPException(400, "Number must contain 10 to 15 digits.")
    data = _lookup("phone", "phoneNumber", number)
    return {"status": "success" if data["count"] else "not_found", "developer": DEVELOPER, "type": "number", "number": number, **data}

@app.get("/aadhar={aadhar}")
def aadhar_lookup(aadhar: str):
    aadhar = aadhar.strip()
    if not AADHAAR_RE.fullmatch(aadhar):
        raise HTTPException(400, "Aadhaar value must contain exactly 12 digits.")
    data = _lookup("aadhar", "aadharNumber", aadhar)
    return {"status": "success" if data["count"] else "not_found", "developer": DEVELOPER, "type": "aadhar", "aadhar": aadhar, **data}
