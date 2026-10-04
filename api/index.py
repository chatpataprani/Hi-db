import os
import re
import time
from typing import Any

import pyarrow as pa
import pyarrow.dataset as ds
from fastapi import FastAPI, HTTPException
from huggingface_hub import HfFileSystem

# Hardcoded 227 GB indexed database used by the uploaded working API.
BUCKET = "buckets/CutehackX/icrm-hitek-full-db-mixed-bucket"

HF_TOKEN = os.getenv("HF_TOKEN") or None
MAX_RESULTS = int(os.getenv("MAX_RESULTS", "25"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "4096"))
DEVELOPER = "chatpataprani"

PHONE_RE = re.compile(r"^\d{10,15}$")
AADHAAR_RE = re.compile(r"^\d{12}$")

app = FastAPI(
    title="Hi-db Fast Lookup API",
    version="4.0",
    description="Fast Parquet index lookup API — Developer: chatpataprani",
)

fs = HfFileSystem(token=HF_TOKEN)

PHONE_FILES = sorted(fs.glob(f"{BUCKET}/idx_phone.*.parquet"))
AADHAAR_FILES = sorted(fs.glob(f"{BUCKET}/idx_aadhaar.*.parquet"))

if not AADHAAR_FILES:
    AADHAAR_FILES = sorted(fs.glob(f"{BUCKET}/idx_aadhar.*.parquet"))

if not PHONE_FILES:
    raise RuntimeError("No idx_phone.*.parquet files were found in the Hugging Face bucket.")

if not AADHAAR_FILES:
    raise RuntimeError("No idx_aadhaar.*.parquet or idx_aadhar.*.parquet files were found.")

phone_dataset = ds.dataset(PHONE_FILES, filesystem=fs, format="parquet")
aadhaar_dataset = ds.dataset(AADHAAR_FILES, filesystem=fs, format="parquet")


def _column_type(dataset: ds.Dataset, column: str) -> pa.DataType:
    try:
        return dataset.schema.field(column).type
    except KeyError as exc:
        raise RuntimeError(
            f"Required index column '{column}' was not found. Available columns: {dataset.schema.names}"
        ) from exc


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


def _lookup(dataset: ds.Dataset, column: str, value: str) -> dict[str, Any]:
    started = time.perf_counter()
    typed = _typed_value(dataset, column, value)
    filter_expr = ds.field(column) == typed
    rows: list[dict[str, Any]] = []

    scanner = dataset.scanner(
        filter=filter_expr,
        batch_size=BATCH_SIZE,
        use_threads=True,
    )

    for batch in scanner.to_batches():
        remaining = MAX_RESULTS - len(rows)
        if remaining <= 0:
            break
        rows.extend(batch.to_pylist()[:remaining])
        if len(rows) >= MAX_RESULTS:
            break

    return {
        "results": rows,
        "count": len(rows),
        "lookup_ms": round((time.perf_counter() - started) * 1000, 2),
    }


@app.get("/")
def root():
    return {
        "status": "online",
        "developer": DEVELOPER,
        "backend": "PyArrow + HuggingFace HfFileSystem",
        "database": BUCKET,
        "endpoints": {
            "number": "/number=<10-15 digit number>",
            "aadhar": "/aadhar=<12 digit Aadhaar>",
        },
        "phone_index_parts": len(PHONE_FILES),
        "aadhar_index_parts": len(AADHAAR_FILES),
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "developer": DEVELOPER,
        "backend": "PyArrow + HuggingFace HfFileSystem",
        "database": BUCKET,
        "phone_index_parts": len(PHONE_FILES),
        "aadhar_index_parts": len(AADHAAR_FILES),
    }


@app.get("/number={number}")
def number_lookup(number: str):
    number = number.strip()
    if not PHONE_RE.fullmatch(number):
        raise HTTPException(400, "Number must contain 10 to 15 digits.")
    data = _lookup(phone_dataset, "phoneNumber", number)
    return {
        "status": "success" if data["count"] else "not_found",
        "developer": DEVELOPER,
        "type": "number",
        "number": number,
        **data,
    }


@app.get("/aadhar={aadhar}")
def aadhar_lookup(aadhar: str):
    aadhar = aadhar.strip()
    if not AADHAAR_RE.fullmatch(aadhar):
        raise HTTPException(400, "Aadhaar value must contain exactly 12 digits.")
    data = _lookup(aadhaar_dataset, "aadharNumber", aadhar)
    return {
        "status": "success" if data["count"] else "not_found",
        "developer": DEVELOPER,
        "type": "aadhar",
        "aadhar": aadhar,
        **data,
    }
