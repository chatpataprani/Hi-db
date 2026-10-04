"""Build compact key/value shards from a synthetic CSV dataset."""
import csv, hashlib, json, os
from pathlib import Path

SHARDS = int(os.getenv("SHARD_COUNT", "256"))
SOURCE_DIR = Path(os.getenv("SOURCE_DIR", "./data"))
OUT_DIR = Path(os.getenv("OUT_DIR", "./indexes"))

def shard(key):
    return int.from_bytes(hashlib.blake2b(key.encode(), digest_size=4).digest(), "big") % SHARDS

def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    buckets = [{} for _ in range(SHARDS)]
    files = list(SOURCE_DIR.rglob("*.csv"))
    if not files:
        raise SystemExit(f"No CSV files found under {SOURCE_DIR}")
    for path in files:
        with path.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                key = (row.get("record_id") or row.get("id") or "").strip()
                if key:
                    buckets[shard(key)].setdefault(key, []).append(row)
    for i, bucket in enumerate(buckets):
        (OUT_DIR / f"records-{i:03d}.json").write_text(
            json.dumps(bucket, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    print(f"Wrote {SHARDS} shards to {OUT_DIR}")

if __name__ == "__main__":
    main()
