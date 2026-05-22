# light_vision

Lightweight vision priority classifier for disaster image tiles.

The classifier takes a pre-disaster and post-disaster image pair, runs three
small vision models, and returns a priority for downstream heavy-model
processing.

## Priorities

```text
3: high
2: medium
1: low
0: no_building
```

## Files

```text
priority_classifier.py
models.py
runs/stage0_best_model.pt
runs/stage1_best_model.pt
runs/stage2_best_model.pt
```

## Install

```bash
pip install -r requirements.txt
```

## Run

From this repository directory:

```bash
python3 priority_classifier.py \
  --pre-image /path/to/tile_pre_disaster.png \
  --post-image /path/to/tile_post_disaster.png \
  --device cpu
```

Or from the parent directory:

```bash
python3 -m light_vision.priority_classifier \
  --pre-image /path/to/tile_pre_disaster.png \
  --post-image /path/to/tile_post_disaster.png \
  --device cpu
```

Default checkpoints are loaded from:

```text
runs/stage0_best_model.pt
runs/stage1_best_model.pt
runs/stage2_best_model.pt
```

## Submit Jobs To Heavy Model API

Agreed API contract:

```text
Method: POST
Endpoint: http://heavy-model-service/jobs
Auth: none
Success response: 200 OK
```

Prepare a CSV or JSONL file with at least:

```text
pre_image
post_image
```

Recommended optional ID fields:

```text
image_id
tile_id
sample_id
```

Dry run:

```bash
python3 api_producer.py \
  --input /path/to/jobs.csv \
  --api-url http://heavy-model-service/jobs \
  --device cpu \
  --dry-run
```

Submit to the REST API:

```bash
python3 api_producer.py \
  --input /path/to/jobs.csv \
  --api-url http://heavy-model-service/jobs \
  --device cpu
```

Payload example:

```json
{
  "image_id": "tile_000123",
  "pre_image_uri": "/data/images/tile_000123_pre.png",
  "post_image_uri": "/data/images/tile_000123_post.png",
  "priority": 3,
  "created_at": "2026-05-22T14:30:00+09:00"
}
```

## Process Selected Images From S3

If selected pre/post images are already uploaded to S3, scan the bucket prefix and
submit prioritized metadata:

```bash
python3 s3_batch_producer.py \
  --bucket your-bucket-name \
  --prefix selected-images/ \
  --api-url http://heavy-model-service/jobs \
  --device cpu
```

Dry run:

```bash
python3 s3_batch_producer.py \
  --bucket your-bucket-name \
  --prefix selected-images/ \
  --api-url http://heavy-model-service/jobs \
  --device cpu \
  --dry-run
```

Expected S3 naming:

```text
selected-images/tile_000123_pre.png
selected-images/tile_000123_post.png
```

Also supported:

```text
*_pre_disaster -> *_post_disaster
*_pre          -> *_post
pre_disaster   -> post_disaster
pre            -> post
```
