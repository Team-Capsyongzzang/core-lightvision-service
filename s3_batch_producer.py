from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath

import boto3
import requests

try:
    from .api_producer import _headers, _post_with_retry
    from .priority_classifier import (
        DEFAULT_STAGE0_CHECKPOINT,
        DEFAULT_STAGE1_CHECKPOINT,
        DEFAULT_STAGE2_CHECKPOINT,
        PriorityClassifier,
    )
except ImportError:
    from api_producer import _headers, _post_with_retry
    from priority_classifier import (
        DEFAULT_STAGE0_CHECKPOINT,
        DEFAULT_STAGE1_CHECKPOINT,
        DEFAULT_STAGE2_CHECKPOINT,
        PriorityClassifier,
    )


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
PAIR_TOKENS = (
    ("_pre_disaster", "_post_disaster"),
    ("_pre", "_post"),
    ("pre_disaster", "post_disaster"),
    ("pre", "post"),
)


def _s3_uri(bucket: str, key: str) -> str:
    return f"s3://{bucket}/{key}"


def _image_id_from_key(key: str) -> str:
    name = PurePosixPath(key).stem
    for pre_token, _ in PAIR_TOKENS:
        if pre_token in name:
            return name.replace(pre_token, "")
    return name


def _post_key_for_pre_key(key: str) -> str | None:
    for pre_token, post_token in PAIR_TOKENS:
        if pre_token in key:
            return key.replace(pre_token, post_token, 1)
    return None


def _list_image_keys(s3_client, bucket: str, prefix: str) -> list[str]:
    keys: list[str] = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if PurePosixPath(key).suffix.lower() in IMAGE_SUFFIXES:
                keys.append(key)
    return keys


def _find_pairs(keys: list[str]) -> list[tuple[str, str]]:
    key_set = set(keys)
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()

    for pre_key in sorted(keys):
        if pre_key in seen:
            continue
        post_key = _post_key_for_pre_key(pre_key)
        if not post_key or post_key not in key_set:
            continue
        pairs.append((pre_key, post_key))
        seen.add(pre_key)
        seen.add(post_key)

    return pairs


def _download_pair(s3_client, bucket: str, pre_key: str, post_key: str, work_dir: Path) -> tuple[Path, Path]:
    pre_path = work_dir / f"pre{PurePosixPath(pre_key).suffix}"
    post_path = work_dir / f"post{PurePosixPath(post_key).suffix}"
    s3_client.download_file(bucket, pre_key, str(pre_path))
    s3_client.download_file(bucket, post_key, str(post_path))
    return pre_path, post_path


def _build_payload(
    bucket: str,
    pre_key: str,
    post_key: str,
    prediction,
) -> dict:
    return {
        "image_id": _image_id_from_key(pre_key),
        "pre_image_uri": _s3_uri(bucket, pre_key),
        "post_image_uri": _s3_uri(bucket, post_key),
        "priority": prediction.priority,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan selected S3 images, assign priority, and submit metadata to a heavy vision REST API."
    )
    parser.add_argument("--bucket", required=True, help="S3 bucket containing selected image data.")
    parser.add_argument("--prefix", default="", help="S3 prefix to scan.")
    parser.add_argument("--api-url", required=True, help="Heavy vision model job ingestion endpoint.")
    parser.add_argument("--region", default=os.getenv("AWS_REGION"))
    parser.add_argument("--dry-run", action="store_true", help="Print payloads without sending HTTP requests.")
    parser.add_argument("--limit", type=int, default=None, help="Optional maximum number of pairs to process.")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP request timeout in seconds.")
    parser.add_argument("--retries", type=int, default=2, help="Number of retries for network or 5xx errors.")
    parser.add_argument("--continue-on-error", action="store_true", help="Keep processing after a failed pair.")
    parser.add_argument("--token-env", default="HEAVY_MODEL_API_TOKEN", help="Environment variable containing bearer token.")
    parser.add_argument("--stage0-checkpoint", type=Path, default=DEFAULT_STAGE0_CHECKPOINT)
    parser.add_argument("--stage1-checkpoint", type=Path, default=DEFAULT_STAGE1_CHECKPOINT)
    parser.add_argument("--stage2-checkpoint", type=Path, default=DEFAULT_STAGE2_CHECKPOINT)
    parser.add_argument("--model", choices=["resnet18", "mobilenet_v3_small"], default="resnet18")
    parser.add_argument("--image-size", type=int, default=320)
    parser.add_argument("--device", default=None)
    parser.add_argument("--has-building-threshold", type=float, default=0.5)
    parser.add_argument("--high-threshold", type=float, default=0.5)
    parser.add_argument("--medium-threshold", type=float, default=0.5)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    s3_client = boto3.client("s3", region_name=args.region)
    keys = _list_image_keys(s3_client, args.bucket, args.prefix)
    pairs = _find_pairs(keys)
    if args.limit is not None:
        pairs = pairs[: args.limit]

    print(f"found image_keys={len(keys)} pairs={len(pairs)}")

    classifier = PriorityClassifier(
        stage0_checkpoint=args.stage0_checkpoint,
        stage1_checkpoint=args.stage1_checkpoint,
        stage2_checkpoint=args.stage2_checkpoint,
        model_name=args.model,
        image_size=args.image_size,
        device=args.device,
        has_building_threshold=args.has_building_threshold,
        high_threshold=args.high_threshold,
        medium_threshold=args.medium_threshold,
    )

    session = requests.Session()
    headers = _headers(args.token_env)
    sent = 0
    failed = 0

    for pre_key, post_key in pairs:
        try:
            with tempfile.TemporaryDirectory() as tmp:
                pre_path, post_path = _download_pair(s3_client, args.bucket, pre_key, post_key, Path(tmp))
                prediction = classifier.predict(pre_path, post_path)
                payload = _build_payload(args.bucket, pre_key, post_key, prediction)

            if args.dry_run:
                print(json.dumps(payload, ensure_ascii=False))
                continue

            response = _post_with_retry(
                session=session,
                api_url=args.api_url,
                payload=payload,
                headers=headers,
                timeout=args.timeout,
                retries=args.retries,
            )
            if response.status_code >= 400:
                raise RuntimeError(f"request failed {response.status_code}: {response.text[:300]}")

            sent += 1
            print(f"submitted image_id={payload['image_id']} priority={payload['priority']}")
        except Exception as exc:
            failed += 1
            print(f"failed pre_key={pre_key}: {exc}", file=sys.stderr)
            if not args.continue_on_error:
                raise

    if not args.dry_run:
        print(f"done submitted={sent} failed={failed}")


if __name__ == "__main__":
    main()
