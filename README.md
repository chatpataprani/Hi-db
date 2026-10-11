# Hi-db

Hi-db uses a **Vercel API + remote Hugging Face Parquet index**. The API does not need to download the full raw dataset or run a separate always-on database server for each search.

## Architecture

1. The raw Parquet files stay in the Hugging Face Storage Bucket.
2. Run `scripts/build_remote_index.py` once to create a sorted, 256-shard index containing only the fields returned by the API.
3. Vercel hashes the requested `account_id` to select exactly one shard.
4. DuckDB queries that remote Parquet shard through Hugging Face's filesystem interface. Sorted row groups allow Parquet statistics to skip irrelevant ranges.

The one-time index build still scans the source data, so run it from a machine/runtime with enough disk space and time. Individual API lookups should only access one shard.

## One-time index build

Run this from a Colab runtime or another machine with sufficient temporary storage. Do not run the full index build inside a Vercel Function.

```bash
pip install duckdb huggingface_hub
export HF_TOKEN="hf_your_write_token"
export HI_DB_BUCKET="Chatpataprani/HITECH_DATABASE-bucket"
python scripts/build_remote_index.py
```

By default, the script looks for Parquet files anywhere inside that bucket. If the raw Parquet files are in a specific folder, set `HI_DB_SOURCE_GLOB`, for example:

```bash
export HI_DB_SOURCE_GLOB="hf://buckets/Chatpataprani/HITECH_DATABASE-bucket/raw/**/*.parquet"
```

Expected source columns: `account_id`, `phone`, `email`, `linked_id`, `linked_name`, and `linked_handle`. The builder casts output columns to strings and omits the other source columns from the lookup index.

## Vercel environment variables

Configure these for the **Production** environment in Vercel, then redeploy:

| Variable | Value |
| --- | --- |
| `HI_DB_BUCKET` | `Chatpataprani/HITECH_DATABASE-bucket` |
| `HI_DB_INDEX_PREFIX` | `index/account_id` |
| `HI_DB_API_KEY` | A long, random secret used by clients as a Bearer token |
| `HF_TOKEN` | Optional for a public bucket; required if the index bucket is private |
| `MAX_RESULTS` | `10` (optional; maximum is 25) |

The lookup route fails closed until `HI_DB_API_KEY` is configured. Because results can contain phone numbers and email addresses, do not expose unrestricted public lookups.

## API

Health check:

```http
GET /health
```

Lookup:

```http
GET /lookup?account_id=YOUR_ACCOUNT_ID&limit=10
Authorization: Bearer YOUR_HI_DB_API_KEY
```

The response includes `count`, `results`, `lookup_ms`, and `index_shard`. The API only accepts exact `account_id` matches and returns `phone`, `email`, `linked_id`, `linked_name`, and `linked_handle` where present.

## Important notes

- Search will not return records until the remote index has been built and uploaded.
- The Vercel deployment being Ready only confirms the API code was deployed; it does not confirm the Hugging Face index exists.
- Keep the Hugging Face write token private. Only add it to Vercel environment variables if the API needs to read a private bucket.
- This is a remote Parquet lookup architecture, not a full database server. Query latency depends on Hugging Face access, cold starts, shard size, and Parquet row-group pruning.
