# Hi-db — 438 GB SQLite Search API

The source database is stored in the Hugging Face bucket
`Chatpataprani/Ind438GB-bucket/users_data.db` (438,408,007,680 bytes).

## Findings so far

- SQLite table: `users`
- Columns: `mobile`, `name`, `fname`, `address`, `alt`, `circle`, `id`, `email`
- Existing indexes: `idx_mobile`, `idx_id`, `idx_email`, `idx_alt`
- Hugging Face remote random reads worked, but a remote SQLite experiment took 1,312 ms for one indexed lookup and then raised an I/O error. Do not use that experimental adapter in production.
- The database must not be downloaded to the user's phone or bundled into Vercel.

## Recommended architecture

1. Keep the original database in Hugging Face as the source copy.
2. Copy it once to a persistent server disk with at least 500 GB available (600 GB is safer for operational headroom). Run SQLite locally against the existing indexes.
3. Run `service/app.py` on that server. It opens the database read-only and only supports exact-match searches on the four existing indexed fields.
4. Run `api/index.py` on Vercel as a lightweight authenticated gateway to the search service.
5. Benchmark cold and warm requests, plus p50/p95 under concurrency, before promising any latency target.

The current service intentionally does not support name/address substring searches because those fields are not indexed. Adding those searches requires a separate indexing design.

## Environment variables

### Persistent search service

- `DB_PATH`: path to the locally stored database; default `/data/users_data.db`
- `API_TOKEN`: long random secret shared only with the Vercel gateway
- `MAX_RESULTS`: result cap from 1 to 25; default 10

### Vercel gateway

- `SEARCH_SERVICE_URL`: base URL of the persistent search service
- `SEARCH_API_TOKEN`: same value as the search service's `API_TOKEN`
- `CLIENT_API_KEY`: client-facing bearer key; lookups fail closed if this is not configured
- `MAX_RESULTS`: result cap from 1 to 25; default 10

Never commit tokens to GitHub. Use the hosting providers' secret/environment-variable settings.

## API endpoints

All lookup endpoints require `Authorization: Bearer <CLIENT_API_KEY>`.

- `GET /lookup?field=mobile&value=...&limit=10`
- `GET /lookup?field=id&value=...&limit=10`
- `GET /lookup?field=email&value=...&limit=10`
- `GET /lookup?field=alt&value=...&limit=10`
- Compatibility routes: `/number=<value>`, `/id=<value>`, `/email=<value>`, `/alt=<value>`
- `GET /health`

The service is read-only and uses parameterized values. The set of SQL column names is an explicit allowlist.

## Time estimates (planning estimates, not measured guarantees)

- One-time transfer of 438 GB: roughly 2–10+ hours depending on sustained throughput, Hugging Face delivery, and the server's disk. At 20 MB/s, the transfer alone is about 6.1 hours; at 100 MB/s, about 1.2 hours before overhead.
- Service setup and validation after the disk is ready: about 1–3 hours.
- First usable deployment: plan for half a day to a full day once a suitable server and credentials are available; slow transfer or provider setup can extend this to 1–2 days.
- Search latency after the database is local: a reasonable initial expectation is single-digit to tens of milliseconds for a warm indexed SQLite query, plus network/API overhead. End-to-end results may be around 30–150 ms depending on region, payload size, and load. This is an estimate, not a benchmark; the under-100 ms target must be verified.

## Status

The service and gateway code are scaffolds committed to the repository, but the 438 GB database has not been copied to a persistent server and no live search deployment has been verified yet. A cloud account with suitable persistent storage is still needed.
