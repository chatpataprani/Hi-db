#!/usr/bin/env python3
"""Download Parquet files from the configured Hugging Face bucket and build the index once."""
import os
import subprocess
import sys
from pathlib import Path

from huggingface_hub import download_bucket_files, list_bucket_tree

BUCKET_ID = os.getenv("HF_BUCKET_ID", "Chatpataprani/Telegram-DB-bucket")
DATA_DIR = Path(os.getenv("PARQUET_DIR", "/data/telegram_parquet"))
DB_PATH = Path(os.getenv("DB_PATH", "/data/telegram_lookup.db"))
PATTERN = str(DATA_DIR / "**" / "*.parquet")


def main() -> None:
    if DB_PATH.is_file() and DB_PATH.stat().st_size > 0:
        print(f"Existing index found at {DB_PATH}; skipping import.", flush=True)
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Listing Parquet files in Hugging Face bucket {BUCKET_ID}...", flush=True)
    files = list_bucket_tree(BUCKET_ID, recursive=True)
    parquet_files = [
        item for item in files
        if getattr(item, "type", None) == "file"
        and str(getattr(item, "path", "")).lower().endswith(".parquet")
    ]
    if not parquet_files:
        raise RuntimeError(
            "No Parquet files were visible in the configured bucket. "
            "Check HF_TOKEN permissions and HF_BUCKET_ID."
        )

    downloads = []
    for item in parquet_files:
        remote_path = str(item.path)
        local_path = DATA_DIR / remote_path
        local_path.parent.mkdir(parents=True, exist_ok=True)
        if not local_path.is_file():
            downloads.append((item, local_path))

    if downloads:
        print(f"Downloading {len(downloads)} Parquet files into persistent storage...", flush=True)
        download_bucket_files(BUCKET_ID, downloads)
    else:
        print("All Parquet files are already present.", flush=True)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, "/app/build_index.py",
        "--input", PATTERN,
        "--output", str(DB_PATH),
    ]
    subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
