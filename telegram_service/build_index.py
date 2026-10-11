#!/usr/bin/env python3
"""Build a compact indexed SQLite lookup DB from the Telegram Parquet files.

Only account_id and permitted result fields are imported; Parquet sources remain unchanged.
"""
import argparse
import sqlite3
import time
from pathlib import Path
import duckdb


def clean(value):
    if value is None:
        return None
    value = str(value).strip()
    return None if not value or value.casefold() == "null" else value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="/data/telegram_parquet/**/*.parquet")
    parser.add_argument("--output", default="/data/telegram_lookup.db")
    parser.add_argument("--batch-size", type=int, default=100_000)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise SystemExit(f"Refusing to overwrite existing output: {output}")
    started = time.perf_counter()
    print(f"Reading Parquet input: {args.input}", flush=True)
    duck = duckdb.connect()
    cursor = duck.execute(
        "SELECT CAST(account_id AS VARCHAR), CAST(phone AS VARCHAR), "
        "CAST(email AS VARCHAR), CAST(linked_id AS VARCHAR), "
        "CAST(linked_name AS VARCHAR), CAST(linked_handle AS VARCHAR) "
        "FROM read_parquet(?, union_by_name=true) WHERE account_id IS NOT NULL",
        [args.input],
    )
    db = sqlite3.connect(str(output))
    inserted = 0
    try:
        db.execute("PRAGMA journal_mode=OFF")
        db.execute("PRAGMA synchronous=OFF")
        db.execute("PRAGMA temp_store=MEMORY")
        db.execute("CREATE TABLE records (account_id TEXT NOT NULL, phone TEXT, email TEXT, linked_id TEXT, linked_name TEXT, linked_handle TEXT)")
        while True:
            batch = cursor.fetchmany(args.batch_size)
            if not batch:
                break
            rows = []
            for values in batch:
                account_id = clean(values[0])
                if account_id is not None:
                    rows.append((account_id, *(clean(v) for v in values[1:])))
            db.executemany("INSERT INTO records VALUES (?, ?, ?, ?, ?, ?)", rows)
            db.commit()
            inserted += len(rows)
            if inserted and inserted % 1_000_000 < len(rows):
                print(f"Imported {inserted:,} rows in {time.perf_counter() - started:.1f}s", flush=True)
        print(f"Creating account_id index for {inserted:,} rows...", flush=True)
        db.execute("CREATE INDEX idx_records_account_id ON records(account_id)")
        db.execute("ANALYZE")
        db.commit()
        db.execute("PRAGMA optimize")
        db.commit()
    except Exception:
        db.close()
        output.unlink(missing_ok=True)
        raise
    finally:
        duck.close()
    db.close()
    print(f"Done: {inserted:,} rows; {output.stat().st_size:,} bytes; {time.perf_counter() - started:.1f}s", flush=True)


if __name__ == "__main__":
    main()
