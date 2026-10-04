"""Build fast lookup shards from extracted synthetic data.

Run this once on a machine/HF Job that has the raw dataset available.
It supports CSV, JSON, JSONL and Parquet. Set KEY_FIELD to the field users
will search (default: record_id, id, uuid, key).
"""
import csv
import hashlib
import json
import os
from pathlib import Path

SHARDS = int(os.getenv("SHARD_COUNT", "256"))
SOURCE_DIR = Path(os.getenv("SOURCE_DIR", "./data"))
OUT_DIR = Path(os.getenv("OUT_DIR", "./indexes"))
KEY_FIELD = os.getenv("KEY_FIELD", "").strip()

CANDIDATE_FIELDS = ("record_id", "id", "uuid", "key")

def shard(key: str) -> int:
    return int.from_bytes(
        hashlib.blake2b(key.encode(), digest_size=4).digest(), "big"
    ) % SHARDS

def key_for(row):
    if KEY_FIELD:
        value = row.get(KEY_FIELD)
        return str(value).strip() if value is not None else ""
    for field in CANDIDATE_FIELDS:
        value = row.get(field)
        if value not in (None, ""):
            return str(value).strip()
    return ""

def add(buckets, row):
    key = key_for(row)
    if key:
        buckets[shard(key)].setdefault(key, []).append(row)

def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    buckets = [{} for _ in range(SHARDS)]

    for path in SOURCE_DIR.rglob("*"):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix == ".csv":
            with path.open("r", encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f):
                    add(buckets, dict(row))
        elif suffix in (".jsonl", ".ndjson"):
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        row = json.loads(line)
                        if isinstance(row, dict):
                            add(buckets, row)
        elif suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            rows = data if isinstance(data, list) else data.get("data", []) if isinstance(data, dict) else []
            for row in rows:
                if isinstance(row, dict):
                    add(buckets, row)
        elif suffix == ".parquet":
            import pyarrow.parquet as pq
            table = pq.read_table(path)
            for row in table.to_pylist():
                add(buckets, row)

    written = 0
    for i, bucket in enumerate(buckets):
        out = OUT_DIR / f"records-{i:03d}.json"
        out.write_text(
            json.dumps(bucket, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        written += len(bucket)

    print(f"Wrote {SHARDS} shards; indexed {written} unique keys")

if __name__ == "__main__":
    main()
