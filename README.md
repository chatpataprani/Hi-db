# Hi-db Fast Lookup API

FastAPI service for a synthetic learning database.

## Architecture

Do **not** scan the 74 GB raw archive on every Vercel request. Build compact lookup shards once, host those shards, and let Vercel perform one small shard request per lookup.

The API supports:

- `/health`
- `/record=VALUE`
- `/record/VALUE`
- `/search?q=VALUE`

Responses include `lookup_ms`.

## Build the index

Run the one-time `hf_indexer.py` job on a machine/Hugging Face Job with enough temporary disk for the extracted dataset.

If the bucket contains split ZIP parts such as `Hi-Tek-DB.zip.001`, `.002`, etc.:

```bash
hf buckets sync hf://buckets/Chatpataprani/HITECH_DATABASE-bucket /data/source
cat /data/source/Hi-Tek-DB.zip.* > /data/Hi-Tek-DB.zip
mkdir -p /data/extracted
unzip -q /data/Hi-Tek-DB.zip -d /data/extracted
```

Then:

```export SOURCE_DIR=/data/extracted
export OUT_DIR=/data/indexes
python hf_indexer.py
```

If the searchable field is not `record_id`, `id`, `uuid`, or `key`, set it explicitly:

```export KEY_FIELD=your_field_name
python hf_indexer.py
```

The result is:

```
indexes/
  records-000.json
  ...
  records-255.json
```

Host those files somewhere reachable over HTTPS, then set this Vercel environment variable:

```
INDEX_BASE_URL=https://your-index-host
```

Optional:

```
INDEX_TOKEN=...
SHARD_COUNT=256
MAX_RESULTS=25
```

## Test

```
https://YOUR-VERCEL-DOMAIN/health
https://YOUR-VERCEL-DOMAIN/search?q=VALUE
```

The raw database stays outside GitHub and Vercel.
