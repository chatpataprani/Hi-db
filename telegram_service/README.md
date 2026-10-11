# Telegram Account ID Lookup

Separate service for the 5.68 GB Telegram Parquet dataset. This does not alter the existing 438 GB SQLite API.

## Search contract

- Search by exact `account_id` only.
- Return `phone` and `email` when present.
- If linked-account data exists, also return `linked_id`, `linked_name`, and `linked_handle` when present.
- Do not return `username`, `first_name`, `last_name`, or `status`.
- Require a bearer API token.

## Fast architecture

Do not scan Parquet files for each API request. Copy the Parquet files once to a persistent server, import only the allowed columns into a compact SQLite lookup database, and create an index on `account_id`. The source Parquet files remain unchanged.

The index builder and API are being added in this folder. Run the index build once on a server with sufficient persistent storage, then benchmark the API. Under 100 ms is a target for warm exact-match requests, not a measured guarantee.
