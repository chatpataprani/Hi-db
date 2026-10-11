#!/usr/bin/env python3
"""
Build a remote-queryable, account_id-sharded Parquet index in a Hugging Face
Storage Bucket. Run this once from a machine with enough temporary disk and
memory for the sort (for example, a suitably provisioned Colab runtime).

The raw dataset remains in Hugging Face. Only the selected output columns are
copied to the index. The API reads one of 256 shards per lookup instead of
downloading the whole raw dataset.

Example:
  pip install duckdb huggingface_hub
  export HF_TOKEN=hf_...
  python scripts/build_remote_index.py

Override HI_DB_SOURCE_GLOB if the raw Parquet files live under a different
folder. Override HI_DB_BUCKET if needed.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import duckdb
from huggingface_hub import HfFileSystem

BUCKET_ID = os.getenv("HI_DB_BUCKET", "Chatpataprani/HITECH_DATABASE-bucket")
SOURCE_GLOB = os.getenv(
    "HI_DB_SOURCE_GLOB",
    f"hf://buckets/{BUCKET_ID}/**/*.parquet",
)
INDEX_PREFIX = os.getenv("HI_DB_INDEX_PREFIX", "index/account_id")
HF_TOKEN = os.getenv("HF_TOKEN", "")
OUTPUT_DIR = Path(os.getenv("HI_DB_LOCAL_INDEX_DIR", "/tmp/hi-db-index"))

COLUMNS = ["account_id", "phone", "email", "linked_id", "linked_name", "linked_handle"]


def sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def main() -> None:
    OUTPUT_DIR.parent.mkdir(parents=True, exist_ok=True)
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True)

    fs = HfFileSystem(token=HF_TOKEN if HF_TOKEN else False)
    con = duckdb.connect()
    con.execute("SET threads=4")
    con.register_filesystem(fs)

    # Check source discovery before starting the large scan.
    source_paths = fs.glob(SOURCE_GLOB.removeprefix("hf://"))
    source_paths = [
        p for p in source_paths
        if p.lower().endswith(".parquet")
        and not f"/{INDEX_PREFIX.strip('/')}/" in f"/{p}/"
    ]
    if not source_paths:
        raise SystemExit(
            f"No source Parquet files found for {SOURCE_GLOB!r}. "
            "Set HI_DB_SOURCE_GLOB to the exact raw-data folder."
        )

    source_list = "[" + ", ".join(sql_string("hf://" + p.lstrip("/")) for p in source_paths) + "]"
    # With PARTITION_BY, DuckDB writes a directory containing shard=xx/data_0.parquet.\n    output_path = str(OUTPUT_DIR).replace("'", "''")
    selected = ", ".join(
        f"CAST({column} AS VARCHAR) AS {column}" for column in COLUMNS
    )
    query = f"""
        COPY (
            SELECT {selected},
                   substr(md5(CAST(account_id AS VARCHAR)), 1, 2) AS shard
            FROM read_parquet({source_list}, union_by_name=true)
            WHERE account_id IS NOT NULL
            ORDER BY shard, CAST(account_id AS VARCHAR)
        )
        TO '{output_path}'
        (FORMAT PARQUET, PARTITION_BY (shard), COMPRESSION ZSTD, ROW_GROUP_SIZE 49152)
    """
    print(f"Found {len(source_paths)} source Parquet files.")
    print("Building sorted, 256-way account_id index; this is a one-time full-data scan...")
    con.execute(query)

    # DuckDB creates shard=xx/data_0.parquet files. Upload each shard to the
    # same bucket; the API reads only the shard matching the query's MD5 prefix.
    local_files = sorted(OUTPUT_DIR.glob("shard=*/data_0.parquet"))
    if not local_files:
        raise SystemExit("Index build produced no shard files. Check DuckDB output path and source schema.")

    remote_root = f"buckets/{BUCKET_ID}/{INDEX_PREFIX.strip('/')}"
    uploaded = 0
    for local_file in local_files:
        shard = local_file.parent.name.split("=", 1)[1]
        remote_file = f"{remote_root}/shard={shard}/data_0.parquet"
        print(f"Uploading shard {shard}: {local_file.stat().st_size:,} bytes")
        with local_file.open("rb") as source, fs.open(remote_file, "wb") as target:
            shutil.copyfileobj(source, target, length=8 * 1024 * 1024)
        uploaded += 1

    print(f"Done. Uploaded {uploaded} shards to hf://{remote_root}/.")
    print("Set HI_DB_BUCKET and HI_DB_INDEX_PREFIX consistently in Vercel.")
    print("The lookup endpoint does not require an API key.")


if __name__ == "__main__":
    main()
