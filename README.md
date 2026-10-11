# Hi-db — Fast SQLite Lookup API

Goal: build a Vercel API for the synthetic database stored in the Hugging Face bucket
`Chatpataprani/Ind438GB-bucket`, with the SQLite file `User_data.db`.

**Latency target:** under 100 ms for warm indexed lookups. This is a benchmark target, not a guarantee; network time, cold starts, and the eventual storage/index design affect actual response time.

## Important architecture note

Do not download or scan a 438 GB database on every Vercel request, and do not bundle it into the Vercel deployment. Vercel functions have limited deployment/runtime storage. We first need the database schema and the exact search key(s), then we can choose the smallest viable indexed/sharded design and benchmark it.

## Step 1 — inspect the SQLite schema

The repo includes `inspect_sqlite.py`. Run it on a machine where `User_data.db` is available:

```bash
python inspect_sqlite.py /path/to/User_data.db > schema-report.json
```

The report contains table/column names, existing index definitions, SQLite page statistics, and file size. It does **not** print record contents or run full-table row counts.

Share the contents of `schema-report.json` here. It should contain schema metadata only; do not upload the database or any real records to GitHub.

## Step 2 — index and benchmark

Once we know the schema and which field(s) need lookup, we can build the correct index builder and API endpoints. The index should be created once and stored separately from the raw database; each request must query only the relevant shard/index, never scan the full source.

After deployment, benchmark warm and cold requests separately. We should report measured p50/p95 latency rather than claim <100 ms before testing.

## Hugging Face bucket

`https://huggingface.co/buckets/Chatpataprani/Ind438GB-bucket`

If the bucket is private, the indexing job will need a Hugging Face read token stored as a secret/environment variable, not committed to this repository.
