# Telegram Account ID Lookup — Chatpataprani

Separate service for the 5.68 GB Telegram Parquet dataset. This does not alter the existing 438 GB SQLite API.

## Search contract

- Search by exact `account_id` only.
- Return `phone` and `email` when present.
- If linked-account data exists, also return `linked_id`, `linked_name`, and `linked_handle` when present.
- Do not return `username`, `first_name`, `last_name`, or `status`.
- Every request requires `Authorization: Bearer <API_TOKEN>`.

## Fast architecture

Do not scan Parquet files on every request. Copy the Parquet files once to a persistent server, import only the permitted fields into a compact SQLite database, and create an index on `account_id`. The API uses that local index for lookups. Source Parquet files remain unchanged.

## Build the index once

Place the 100 Parquet files under `/data/telegram_parquet/` on the server, then install dependencies and run:

```bash
cd telegram_service
pip install -r requirements.txt
python build_index.py --input '/data/telegram_parquet/**/*.parquet' --output /data/telegram_lookup.db
```

The builder reads in batches, imports only six fields (`account_id`, `phone`, `email`, `linked_id`, `linked_name`, `linked_handle`), creates an account ID index, and prints row count, database size, and elapsed time. Do not remove the original Parquet files until you have verified and backed up the generated index.

## Run the API

```bash
export API_TOKEN='use-a-long-random-secret'
export DB_PATH=/data/telegram_lookup.db
uvicorn app:app --host 0.0.0.0 --port 8000 --workers 1
```

Request example:

```http
GET /lookup?account_id=1243199268
Authorization: Bearer <API_TOKEN>
```

Example response shape (values illustrative):

```json
{
  "developer": "chatpataprani",
  "status": "success",
  "count": 1,
  "lookup_ms": 0.8,
  "results": [
    {
      "phone": "000000000000",
      "email": "example@example.test",
      "linked_id": "1248726622",
      "linked_name": "Example Linked Account",
      "linked_handle": "example_handle"
    }
  ]
}
```

Missing fields are omitted. Names and usernames from the main account, and status, are never returned.

## Deployment and performance

Use a server with persistent disk. The SQLite database must not be included in a serverless deployment bundle or stored on the user's phone. Under 100 ms is a target for warm exact-match lookups, not a measured guarantee; benchmark after the index is built. This repository contains the service and index-builder code, but the dataset has not been copied and the service has not yet been deployed or benchmarked.
