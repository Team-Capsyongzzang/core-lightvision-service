"""
main.py
=======
SQS 폴링 루프. S3에 새 이미지가 업로드되면 EventBridge → SQS → 여기로 옴.
pre/post 페어를 찾아 분류 후 vision-service에 전송.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath

import boto3
import requests

try:
    from priority_classifier import (
        DEFAULT_STAGE0_CHECKPOINT, DEFAULT_STAGE1_CHECKPOINT, DEFAULT_STAGE2_CHECKPOINT,
        PriorityClassifier,
    )
    from api_producer import _headers, _post_with_retry
    from s3_batch_producer import PAIR_TOKENS, _image_id_from_key
except ImportError:
    from .priority_classifier import (
        DEFAULT_STAGE0_CHECKPOINT, DEFAULT_STAGE1_CHECKPOINT, DEFAULT_STAGE2_CHECKPOINT,
        PriorityClassifier,
    )
    from .api_producer import _headers, _post_with_retry
    from .s3_batch_producer import PAIR_TOKENS, _image_id_from_key


QUEUE_URL  = os.environ["SQS_QUEUE_URL"]
VISION_URL = os.environ.get("VISION_API_URL", "http://vision-service:8000/jobs")
REGION     = os.environ.get("AWS_REGION", "ap-northeast-2")


def _to_pre_key(key: str) -> str | None:
    for pre_token, post_token in PAIR_TOKENS:
        if post_token in key:
            return key.replace(post_token, pre_token, 1)
    return None


def _to_post_key(key: str) -> str | None:
    for pre_token, post_token in PAIR_TOKENS:
        if pre_token in key:
            return key.replace(pre_token, post_token, 1)
    return None


def _is_pre(key: str) -> bool:
    return any(pre in key for pre, _ in PAIR_TOKENS)


def _process(s3, classifier, session, bucket: str, pre_key: str, post_key: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        pre_path  = Path(tmp) / f"pre{PurePosixPath(pre_key).suffix}"
        post_path = Path(tmp) / f"post{PurePosixPath(post_key).suffix}"
        s3.download_file(bucket, pre_key,  str(pre_path))
        s3.download_file(bucket, post_key, str(post_path))
        prediction = classifier.predict(pre_path, post_path)

    payload = {
        "image_id":       _image_id_from_key(pre_key),
        "pre_image_uri":  f"s3://{bucket}/{pre_key}",
        "post_image_uri": f"s3://{bucket}/{post_key}",
        "priority":       prediction.priority,
        "created_at":     datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    _post_with_retry(session, VISION_URL, payload, _headers(None), timeout=10.0, retries=2)
    print(f"[lightvision] submitted image_id={payload['image_id']} priority={prediction.priority}")


def main() -> None:
    sqs        = boto3.client("sqs", region_name=REGION)
    s3         = boto3.client("s3",  region_name=REGION)
    session    = requests.Session()
    classifier = PriorityClassifier(
        stage0_checkpoint=DEFAULT_STAGE0_CHECKPOINT,
        stage1_checkpoint=DEFAULT_STAGE1_CHECKPOINT,
        stage2_checkpoint=DEFAULT_STAGE2_CHECKPOINT,
    )

    print(f"[lightvision] polling {QUEUE_URL}")

    while True:
        resp     = sqs.receive_message(QueueUrl=QUEUE_URL, MaxNumberOfMessages=10, WaitTimeSeconds=20)
        messages = resp.get("Messages", [])

        for msg in messages:
            receipt = msg["ReceiptHandle"]
            try:
                body   = json.loads(msg["Body"])
                detail = body.get("detail", {})
                bucket = detail["bucket"]["name"]
                key    = detail["object"]["key"]

                if _is_pre(key):
                    pre_key, post_key = key, _to_post_key(key)
                else:
                    pre_key, post_key = _to_pre_key(key), key

                if not pre_key or not post_key:
                    print(f"[lightvision] pair 없음, 건너뜀: {key}", file=sys.stderr)
                    sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=receipt)
                    continue

                _process(s3, classifier, session, bucket, pre_key, post_key)
                sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=receipt)

            except s3.exceptions.NoSuchKey:
                # 페어 파일이 아직 업로드 안 됨 → visibility timeout 후 재시도
                print(f"[lightvision] 페어 대기 중: {key}", file=sys.stderr)
            except Exception as exc:
                print(f"[lightvision] 처리 실패: {exc}", file=sys.stderr)
                sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=receipt)


if __name__ == "__main__":
    main()
