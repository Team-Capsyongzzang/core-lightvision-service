# core-lightvision-service

CORE (Cloud-Optimized Resource-Efficient Vision System)  
위성 이미지 재난 분석 파이프라인의 경량 우선순위 분류 서비스.

> 무거운 탐지/분류 모델을 실행하기 전에 pre/post 이미지 쌍을 빠르게 스크리닝합니다.  
> 이 리포는 **lightweight priority classifier**와 **vision-service 작업 제출기**를 담당합니다.

---

## 구조

```
core-lightvision-service/
├── main.py                  ← SQS 폴링 워커: S3 이벤트 수신 → pre/post 페어 분류 → vision-service 전송
├── priority_classifier.py   ← 3단계 우선순위 분류기 및 단건 CLI
├── api_producer.py          ← CSV/JSONL 입력 분류 후 POST /jobs 제출
├── s3_batch_producer.py     ← S3 prefix 스캔 후 이미지 페어 분류 및 제출
├── models.py                ← ResNet18 / MobileNetV3-Small 6채널 모델 팩토리
├── runs/                    ← 학습된 경량 모델 체크포인트
│   ├── stage0_best_model.pt
│   ├── stage1_best_model.pt
│   └── stage2_best_model.pt
├── sampled/                 ← 샘플 pre/post 이미지
├── k8s/
│   ├── deployment.yaml      ← EKS 배포 설정
│   └── serviceaccount.yaml  ← IRSA ServiceAccount
├── Dockerfile
└── requirements.txt
```

---

## 흐름

```
S3 selected-images 업로드
    ↓ EventBridge → SQS
core-lightvision-service 워커(main.py)
    ↓ pre/post 이미지 페어 확인
S3에서 이미지 다운로드
    ↓
3단계 경량 분류기
  stage0: 건물 없음 / 건물 있음
  stage1: high 여부
  stage2: low / medium
    ↓
priority 산정
  3(high) → 2(medium) → 1(low) → 0(no_building)
    ↓ POST /jobs
core-vision-service
```

---

## 우선순위

| priority | label | 설명 |
|---|---|---|
| `3` | `high` | 무거운 모델에서 가장 먼저 처리 |
| `2` | `medium` | 중간 우선순위 |
| `1` | `low` | 낮은 우선순위 |
| `0` | `no_building` | 건물 없음으로 판단 |

분류기는 pre-disaster RGB 이미지와 post-disaster RGB 이미지를 합쳐 **6채널 입력**으로 사용합니다.  
기본 모델은 `resnet18`, 입력 크기는 `320x320`입니다.

---

## vision-service API 계약

`core-lightvision-service`는 분류 결과를 `core-vision-service`의 작업 큐 API로 전송합니다.

```text
Method: POST
Endpoint: http://vision-service:8000/jobs
Auth: 기본값 없음
```

**Request**
```json
{
  "image_id": "tile_000123",
  "pre_image_uri": "s3://your-bucket/selected-images/tile_000123_pre.png",
  "post_image_uri": "s3://your-bucket/selected-images/tile_000123_post.png",
  "priority": 3,
  "created_at": "2026-05-22T14:30:00+09:00"
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `image_id` | string | 이미지 쌍 ID |
| `pre_image_uri` | string | 재난 전 이미지 URI |
| `post_image_uri` | string | 재난 후 이미지 URI |
| `priority` | int | 3=high / 2=medium / 1=low / 0=no_building |
| `created_at` | string | job 생성 시간 (ISO 8601) |

---

## 로컬 실행

```bash
# 가상환경
python -m venv venv
source venv/bin/activate

# 의존성 설치
pip install -r requirements.txt
```

### 단건 이미지 분류

```bash
python3 priority_classifier.py \
  --pre-image sampled/high_joplin-tornado_00000000_pre.png \
  --post-image sampled/high_joplin-tornado_00000000_post.png \
  --device cpu
```

출력 예시는 다음과 같습니다.

```json
{
  "priority": 3,
  "label": "high",
  "stage0_label": "has_building",
  "stage1_label": "high",
  "stage2_label": null,
  "probabilities": {
    "stage0": {
      "no_building": 0.01,
      "has_building": 0.99
    },
    "stage1": {
      "non_high": 0.12,
      "high": 0.88
    }
  }
}
```

기본 체크포인트 경로:

```text
runs/stage0_best_model.pt
runs/stage1_best_model.pt
runs/stage2_best_model.pt
```

### CSV/JSONL 입력을 vision-service로 제출

입력 파일은 최소한 다음 필드를 포함해야 합니다.

```text
pre_image
post_image
```

권장 ID 필드:

```text
image_id
tile_id
sample_id
```

Dry run:

```bash
python3 api_producer.py \
  --input /path/to/jobs.csv \
  --api-url http://vision-service:8000/jobs \
  --device cpu \
  --dry-run
```

실제 제출:

```bash
python3 api_producer.py \
  --input /path/to/jobs.csv \
  --api-url http://vision-service:8000/jobs \
  --device cpu
```

### S3 prefix 배치 처리

S3에 선택된 pre/post 이미지가 이미 올라가 있다면 prefix를 스캔해서 페어를 찾고, 각 페어의 우선순위를 계산해 제출할 수 있습니다.

```bash
python3 s3_batch_producer.py \
  --bucket your-bucket-name \
  --prefix selected-images/ \
  --api-url http://vision-service:8000/jobs \
  --region ap-northeast-2 \
  --device cpu
```

Dry run:

```bash
python3 s3_batch_producer.py \
  --bucket your-bucket-name \
  --prefix selected-images/ \
  --api-url http://vision-service:8000/jobs \
  --region ap-northeast-2 \
  --device cpu \
  --dry-run
```

지원하는 파일명 페어 규칙:

```text
*_pre_disaster  → *_post_disaster
*_pre           → *_post
pre_disaster    → post_disaster
pre             → post
```

---

## 운영 실행

`main.py`는 SQS를 계속 폴링하는 워커입니다. S3 업로드 이벤트가 SQS에 들어오면 해당 객체 key를 기준으로 pre/post 페어를 찾고, 분류 후 `VISION_API_URL`로 작업을 제출합니다.

```bash
export SQS_QUEUE_URL=https://sqs.ap-northeast-2.amazonaws.com/264935030068/my-mlops-prod-lightvision
export VISION_API_URL=http://vision-service:8000/jobs
export AWS_REGION=ap-northeast-2

python3 main.py
```

---

## 환경변수

```env
# SQS 이벤트 큐
SQS_QUEUE_URL=https://sqs.ap-northeast-2.amazonaws.com/264935030068/my-mlops-prod-lightvision

# 결과 전송 대상(core-vision-service)
VISION_API_URL=http://vision-service:8000/jobs

# AWS 리전
AWS_REGION=ap-northeast-2

# api_producer.py / s3_batch_producer.py에서 bearer token을 쓸 경우
HEAVY_MODEL_API_TOKEN=
```

---

## 주요 옵션

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--stage0-checkpoint` | `runs/stage0_best_model.pt` | 건물 유무 분류 체크포인트 |
| `--stage1-checkpoint` | `runs/stage1_best_model.pt` | high 여부 분류 체크포인트 |
| `--stage2-checkpoint` | `runs/stage2_best_model.pt` | low/medium 분류 체크포인트 |
| `--model` | `resnet18` | `resnet18` 또는 `mobilenet_v3_small` |
| `--image-size` | `320` | 모델 입력 이미지 크기 |
| `--device` | 자동 선택 | `cuda`, `cpu` 등 PyTorch 디바이스 |
| `--has-building-threshold` | `0.5` | stage0 건물 있음 판정 기준 |
| `--high-threshold` | `0.5` | stage1 high 판정 기준 |
| `--medium-threshold` | `0.5` | stage2 medium 판정 기준 |

---

## 배포

### Docker

```bash
docker build -t core-lightvision-service .
docker run --rm \
  -e SQS_QUEUE_URL=$SQS_QUEUE_URL \
  -e VISION_API_URL=http://vision-service:8000/jobs \
  -e AWS_REGION=ap-northeast-2 \
  core-lightvision-service
```

### Kubernetes/EKS

현재 배포 설정은 `k8s/deployment.yaml`과 `k8s/serviceaccount.yaml`에 있습니다.

```text
Deployment 이름 : lightvision-service
Namespace       : disaster-monitor
실행 명령       : python main.py
ECR 이미지      : 264935030068.dkr.ecr.ap-northeast-2.amazonaws.com/my-mlops-prod-lightvision-service:latest
ServiceAccount  : lightvision-sa
IAM Role        : my-mlops-prod-lightvision-pod-role
SQS Queue       : my-mlops-prod-lightvision
전송 대상       : http://vision-service:8000/jobs
리소스 요청     : cpu 500m, memory 1Gi
리소스 제한     : cpu 1, memory 2Gi
```

배포:

```bash
kubectl apply -f k8s/serviceaccount.yaml
kubectl apply -f k8s/deployment.yaml
```

---

## 관련 리포

| 리포 | 역할 |
|---|---|
| `core-lightvision-service` | 경량 우선순위 분류 및 vision-service 작업 제출 (이 리포) |
| `core-vision-service` | 무거운 탐지/분류 추론 서빙 |
| `core-dashboard` | 재난 분석 결과 대시보드 |
