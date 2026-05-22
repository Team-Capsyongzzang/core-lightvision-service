from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable

import requests

try:
    from .priority_classifier import (
        DEFAULT_STAGE0_CHECKPOINT,
        DEFAULT_STAGE1_CHECKPOINT,
        DEFAULT_STAGE2_CHECKPOINT,
        PriorityClassifier,
        PriorityPrediction,
    )
except ImportError:
    from priority_classifier import (
        DEFAULT_STAGE0_CHECKPOINT,
        DEFAULT_STAGE1_CHECKPOINT,
        DEFAULT_STAGE2_CHECKPOINT,
        PriorityClassifier,
        PriorityPrediction,
    )


def _read_jobs(path: Path) -> Iterable[dict]:
    if path.suffix.lower() == ".jsonl":
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped:
                    yield json.loads(stripped)
        return

    with path.open("r", encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f)


def _job_id(job: dict, index: int) -> str:
    value = job.get("image_id") or job.get("tile_id") or job.get("sample_id")
    return str(value) if value else f"job_{index:06d}"


def _build_payload(
    job: dict,
    prediction: PriorityPrediction,
    index: int,
) -> dict:
    pre_image = job["pre_image"]
    post_image = job["post_image"]
    return {
        "image_id": _job_id(job, index),
        "pre_image_uri": job.get("pre_image_uri") or pre_image,
        "post_image_uri": job.get("post_image_uri") or post_image,
        "priority": prediction.priority,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def _headers(token_env: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if token_env:
        token = os.getenv(token_env)
        if token:
            headers["Authorization"] = f"Bearer {token}"
    return headers


def _post_with_retry(
    session: requests.Session,
    api_url: str,
    payload: dict,
    headers: dict[str, str],
    timeout: float,
    retries: int,
) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = session.post(api_url, json=payload, headers=headers, timeout=timeout)
            if response.status_code < 500:
                return response
            last_exc = RuntimeError(f"server error {response.status_code}: {response.text[:300]}")
        except requests.RequestException as exc:
            last_exc = exc

        if attempt < retries:
            time.sleep(min(2**attempt, 10))

    raise RuntimeError(f"failed to post job after {retries + 1} attempts: {last_exc}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Classify tile jobs and submit priority metadata to a heavy vision REST API."
    )
    parser.add_argument("--input", type=Path, required=True, help="CSV or JSONL with pre_image and post_image fields.")
    parser.add_argument("--api-url", required=True, help="Heavy vision model job ingestion endpoint.")
    parser.add_argument("--dry-run", action="store_true", help="Print payloads without sending HTTP requests.")
    parser.add_argument("--limit", type=int, default=None, help="Optional maximum number of input jobs to process.")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP request timeout in seconds.")
    parser.add_argument("--retries", type=int, default=2, help="Number of retries for network or 5xx errors.")
    parser.add_argument("--continue-on-error", action="store_true", help="Keep processing after a failed API request.")
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

    for index, job in enumerate(_read_jobs(args.input)):
        if args.limit is not None and index >= args.limit:
            break

        prediction = classifier.predict(job["pre_image"], job["post_image"])
        payload = _build_payload(job, prediction, index)

        if args.dry_run:
            print(json.dumps(payload, ensure_ascii=False))
            continue

        try:
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
        except Exception as exc:
            failed += 1
            print(f"failed image_id={payload['image_id']}: {exc}", file=sys.stderr)
            if not args.continue_on_error:
                raise
            continue

        sent += 1
        print(f"submitted image_id={payload['image_id']} priority={payload['priority']}")

    if not args.dry_run:
        print(f"done submitted={sent} failed={failed}")


if __name__ == "__main__":
    main()
