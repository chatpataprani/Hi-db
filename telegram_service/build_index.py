#!/usr/bin/env python3
"""Build a compact, indexed SQLite lookup DB from Telegram Parquet files.

Only account_id, phone and email are imported. Source Parquet files are not
modified. Run this once on the server after placing the Parquet files there.
"""
import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

import duckdb


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        default="/data/telegram_parquet/**/*.parquet",
        help="Parquet glob, e.g. /data/telegram_parquet/**/*.parquet",
    )
    parser.add_argument(
        "--output",
        default="/data/telegram_lookup.db",
        help="Output SQLite database path",
    )
    parser.add_argument("--batch-size", type=int, default=100_000)
    args = parser.parse_args()

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        backup = output.with_suffix(output.suffix + ".old")
        raise SystemExit(
            f"Refusing to overwrite {output}. Move or remove it first "
            f"(existing file left untouched)."
        )

    started = time.perf_counter()
    print(f"Reading Parquet input: {args.input}", flush=True)
    con = duckdb.connect()
    # Projection keeps only the three required columns. union_by_name allows
    # Parquet files with compatible but differently ordered columns.
    relation = con.sql(
        "SELECT CAST(account_id AS VARCHAR) AS account_id, "
        "NULLIF(NULLIF(TRIM(CAST(phone AS VARCHAR)), ''), 'null') AS phone, "
        "NULLIF(NULLIF(TRIM(CAST(email AS VARCHAR)), ''), 'null') AS email "
        "FROM read_parquet(?, union_by_name = true) "
        "WHERE account_id IS NOT NULL",
        params=[args.input],
    )

    db = sqlite3.connect(str(output))
    try:
        db.execute("PRAGMA journal_mode = OFF")
        db.execute("PRAGMA synchronous = OFF")
        db.execute("PRAGMA temp_store = MEMORY")
        db.execute("PRAGMA cache_size = -262144")
        db.execute(
            "CREATE TABLE records ("
            "account_id TEXT NOT NULL, "
            "phone TEXT, "
            "email TEXT)"
        )
        cursor = relation.execute()
        inserted = 0
        while True:
            batch = cursor.fetchmany(args.batch_size)
            if not batch:
                break
            cleaned = []
            for account_id, phone, email in batch:
                # Some exports encode missing values as the literal "null".
                phone = phone if phone and phone.casefold() != "null" else None
                email = email if email and email.casefold() != "null" else None
                cleaned.append((account_id, phone, email))
            db.executemany(
                "INSERT INTO records(account_id, phone, email) VALUES (?, ?, ?)",
                cleaned,
            )
            db.commit()
            inserted += len(cleaned)
            if inserted % 1_000_000 < len(cleaned):
                elapsed = time.perf_counter() - started
                print(f"Imported {inserted:,} rows in {elapsed:.1f}s", flush=True)

        print(f"Creating account_id index for {inserted:,} rows...", flush=True)
        db.execute("CREATE INDEX idx_records_account_id ON records(account_id)")
        db.execute("ANALYZE")
        db.commit()
        db.execute("PRAGMA optimize")
        db.commit()
    except Exception:
        db.close()
        try:
            output.unlink()
        except OSError:
            pass
        raise
    finally:
        try:
            con.close()
        except Exception:
            pass
    db.close()
    elapsed = time.perf_counter() - started
    print(
        f"Done. rows={inserted:,} db={output} "
        f"size={output.stat().st_size:,} bytes elapsed={elapsed:.1f}s",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
