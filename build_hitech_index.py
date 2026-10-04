#!/usr/bin/env python3
"""
One-time index builder for Chatpataprani/HITECH_DATABASE-bucket.

The 75 GB source is a multipart ZIP. This script:
1. downloads Hi-Tek-DB.zip.001, .002, ... from the HF bucket;
2. concatenates the parts into one ZIP;
3. reads CSV/JSONL/JSON members in batches;
4. creates hash-partitioned Parquet indexes for phoneNumber and aadharNumber.

Run this on a machine with enough disk (at least the source size plus working
space) and enough CPU/RAM. Do NOT run it inside a Vercel function.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
import zipfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from huggingface_hub import HfFileSystem

BUCKET = "buckets/Chatpataprani/HITECH_DATABASE-bucket"
PREFIX = "Hi-Tek-DB.zip."
PART_COUNT = int(os.getenv("PART_COUNT", "36"))
SHARDS = int(os.getenv("SHARDS", "256"))
BATCH_ROWS = int(os.getenv("BATCH_ROWS", "50000"))

WORK = Path(os.getenv("WORK_DIR", "./hitech-index-work"))
PARTS = WORK / "parts"
RAW_ZIP = WORK / "Hi-Tek-DB.zip"
OUT = WORK / "indexes"
HF_TOKEN = os.getenv("HF_TOKEN") or None

PHONE_KEYS = ("phoneNumber", "phone", "number", "mobile", "phone_number")
AADHAAR_KEYS = ("aadharNumber", "aadhaarNumber", "aadhaar", "aadhar", "aadhar_number")


def download_parts(fs: HfFileSystem) -> None:
    PARTS.mkdir(parents=True, exist_ok=True)
    with RAW_ZIP.open("wb") as dst:
        for i in range(1, PART_COUNT + 1):
            remote = f"{BUCKET}/{PREFIX}{i:03d}"
            local = PARTS / f"{PREFIX}{i:03d}"
            if not local.exists():
                print("downloading", remote)
                with fs.open(remote, "rb") as src:
                    shutil.copyfileobj(src, local.open("wb"), length=16 * 1024 * 1024)
            print("joining", local)
            with local.open("rb") as src:
                shutil.copyfileobj(src, dst, length=16 * 1024 * 1024)


def first_key(row: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def write_batches(rows: list[dict], out_dir: Path, key: str, counters: list[int]) -> None:
    if not rows:
        return
    table = pa.Table.from_pylist(rows)
    for shard in range(SHARDS):
        # Partition in Python so each request only opens one small shard.
        selected = [r for r in rows if int.from_bytes(
            hashlib.blake2b(str(r[key]).encode(), digest_size=4).digest(), "big"
        ) % SHARDS == shard]
        if not selected:
            continue
        shard_dir = out_dir / key
        shard_dir.mkdir(parents=True, exist_ok=True)
        part = counters[shard]
        pq.write_table(
            pa.Table.from_pylist(selected),
            shard_dir / f"{shard:03d}-{part:05d}.parquet",
            compression="zstd",
            use_dictionary=True,
        )
        counters[shard] += 1


def process_csv(zf: zipfile.ZipFile, name: str) -> None:
    with zf.open(name, "r") as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="")
        reader = csv.DictReader(text)
        phone_rows: list[dict] = []
        aadhaar_rows: list[dict] = []
        pc = [0] * SHARDS
        ac = [0] * SHARDS
        for row in reader:
            phone = first_key(row, PHONE_KEYS)
            aadhaar = first_key(row, AADHAAR_KEYS)
            if phone:
                row["phoneNumber"] = phone
                phone_rows.append(row)
            if aadhaar:
                row["aadharNumber"] = aadhaar
                aadhaar_rows.append(row)
            if len(phone_rows) >= BATCH_ROWS:
                write_batches(phone_rows, OUT, "phoneNumber", pc)
                phone_rows.clear()
            if len(aadhaar_rows) >= BATCH_ROWS:
                write_batches(aadhaar_rows, OUT, "aadharNumber", ac)
                aadhaar_rows.clear()
        write_batches(phone_rows, OUT, "phoneNumber", pc)
        write_batches(aadhaar_rows, OUT, "aadharNumber", ac)


def process_jsonl(zf: zipfile.ZipFile, name: str) -> None:
    with zf.open(name, "r") as raw:
        phone_rows: list[dict] = []
        aadhaar_rows: list[dict] = []
        pc = [0] * SHARDS
        ac = [0] * SHARDS
        for line in io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace"):
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                continue
            phone = first_key(row, PHONE_KEYS)
            aadhaar = first_key(row, AADHAAR_KEYS)
            if phone:
                row["phoneNumber"] = phone
                phone_rows.append(row)
            if aadhaar:
                row["aadharNumber"] = aadhaar
                aadhaar_rows.append(row)
            if len(phone_rows) >= BATCH_ROWS:
                write_batches(phone_rows, OUT, "phoneNumber", pc)
                phone_rows.clear()
            if len(aadhaar_rows) >= BATCH_ROWS:
                write_batches(aadhaar_rows, OUT, "aadharNumber", ac)
                aadhaar_rows.clear()
        write_batches(phone_rows, OUT, "phoneNumber", pc)
        write_batches(aadhaar_rows, OUT, "aadharNumber", ac)


def main() -> None:
    fs = HfFileSystem(token=HF_TOKEN)
    download_parts(fs)
    print("opening", RAW_ZIP)
    with zipfile.ZipFile(RAW_ZIP) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        supported = [n for n in names if n.lower().endswith((".csv", ".jsonl", ".ndjson"))]
        if not supported:
            raise RuntimeError("No CSV/JSONL members found in the multipart ZIP; inspect the archive format first.")
        for name in supported:
            print("processing", name)
            if name.lower().endswith(".csv"):
                process_csv(zf, name)
            else:
                process_jsonl(zf, name)
    print("Indexes created under", OUT)
    print("Upload OUT/phoneNumber/*.parquet to", BUCKET + "/indexes/idx_phone/")
    print("Upload OUT/aadharNumber/*.parquet to", BUCKET + "/indexes/idx_aadhaar/")


if __name__ == "__main__":
    main()
