#!/usr/bin/env python3
"""Inspect a local SQLite database without printing any row contents.

Usage:
    python inspect_sqlite.py /path/to/User_data.db

This reports schema, existing indexes, SQLite page statistics, and file size.
It deliberately does not dump records or run full-table COUNT(*) scans.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python inspect_sqlite.py /path/to/User_data.db", file=sys.stderr)
        return 2

    db_path = Path(sys.argv[1]).expanduser().resolve()
    if not db_path.is_file():
        print(json.dumps({"error": "File not found", "path": str(db_path)}))
        return 1

    uri = db_path.as_uri() + "?mode=ro"
    report: dict = {
        "file_name": db_path.name,
        "file_size_bytes": db_path.stat().st_size,
        "tables": [],
    }

    try:
        conn = sqlite3.connect(uri, uri=True, timeout=30)
        conn.execute("PRAGMA query_only = ON")
        report["sqlite_version"] = sqlite3.sqlite_version
        report["page_size"] = conn.execute("PRAGMA page_size").fetchone()[0]
        report["page_count"] = conn.execute("PRAGMA page_count").fetchone()[0]
        report["freelist_count"] = conn.execute("PRAGMA freelist_count").fetchone()[0]
        names = conn.execute(
            "SELECT name, type FROM sqlite_master "
            "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' "
            "ORDER BY type, name"
        ).fetchall()

        for name, kind in names:
            entry = {"name": name, "type": kind}
            if kind == "table":
                entry["columns"] = [
                    {
                        "name": row[1],
                        "type": row[2],
                        "not_null": bool(row[3]),
                        "primary_key_position": row[5],
                    }
                    for row in conn.execute(
                        f"PRAGMA table_info({json.dumps(name)})"
                    ).fetchall()
                ]
                entry["indexes"] = []
                for index_row in conn.execute(
                    f"PRAGMA index_list({json.dumps(name)})"
                ).fetchall():
                    index_name = index_row[1]
                    index_info = conn.execute(
                        f"PRAGMA index_info({json.dumps(index_name)})"
                    ).fetchall()
                    entry["indexes"].append({
                        "name": index_name,
                        "unique": bool(index_row[2]),
                        "columns": [col[2] for col in index_info],
                    })
            report["tables"].append(entry)

        conn.close()
        print(json.dumps(report, indent=2))
        return 0
    except sqlite3.Error as exc:
        print(json.dumps({"error": "SQLite inspection failed", "detail": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
