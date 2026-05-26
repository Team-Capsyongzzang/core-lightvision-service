# core-vision-service

CORE (Cloud-Optimized Resource-Efficient Vision System)  
위성 이미지 재난 탐지 및 분류 추론 서버.

> 학습/실험 코드는 별도 리포(`core-vision-experiment`)에서 관리합니다.  
> 이 리포는 **추론 서빙**만 담당합니다.

---

## 구조

```
core-vision-service/
├── main.py              ← FastAPI 진입점
├── api/
│   └── jobs.py          ← POST /jobs (민규 스크리닝 결과 수신)
├── core/
│   ├── pipeline.py      ← 탐지기 + 분류기 추론
│   ├── queue_manager.py ← 우선순위 큐
│   └── worker.py        ← 큐 처리 → S3 이미지 로드 → 추론 → 대시보드 전송
├── models/
│   ├── backbone.py      ← 9개 백본 팩토리
│   ├── detector.py      ← 재난 탐지기 (이진 분류)
│   └── classifier.py    ← 재난 분류기 (7 classes)
├── config/
│   └── config.py        ← 환경변수 기반 설정
├── checkpoints/         ← .gitignore (EBS PVC로 마운트)
│   ├── detector/best_ft.pth
│   └── classifier/best_ft.pth
├── Dockerfile
├── requirements.txt
└── .env.example
```

---

## 흐름

```
민규 스크리닝 서버
    ↓ POST /jobs
우선순위 큐
  priority 3(high) → 2(medium) → 1(low) → 0(no_building)
  같은 priority면 created_at 빠른 것부터
    ↓ 워커
S3에서 이미지 다운로드
    ↓
탐지기(ResNet50) → 분류기(ResNet101)
    ↓ POST /api/ingest
대시보드 서버 (core-dashboard)
```

---

## API

### POST /jobs

민규 스크리닝 서버에서 호출하는 엔드포인트.

**Request**
```json
{
  "image_id":       "tile_000123",
  "pre_image_uri":  "s3://your-bucket/selected-images/tile_000123_pre.png",
  "post_image_uri": "s3://your-bucket/selected-images/tile_000123_post.png",
  "priority":       3,
  "created_at":     "2026-05-22T14:30:00+09:00"
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `image_id` | string | 이미지 쌍 ID |
| `pre_image_uri` | string | 재난 전 이미지 S3 URI |
| `post_image_uri` | string | 재난 후 이미지 S3 URI |
| `priority` | int | 3=high / 2=medium / 1=low / 0=no_building |
| `created_at` | string | job 생성 시간 (ISO 8601) |

**Response**
```json
// 성공 (HTTP 200)
{ "status": "accepted", "image_id": "tile_000123" }

// 실패 (HTTP 400)
{ "status": "error", "message": "missing pre_image_uri" }
```

### GET /api/health

```json
{
  "status":   "ok",
  "pipeline": true,
  "queue": {
    "waiting":    3,
    "processing": 1,
    "completed":  42,
    "failed":     0
  }
}
```

---

## 로컬 실행

```bash
# 가상환경
python -m venv venv
source venv/bin/activate

# PyTorch (CUDA 12.1)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
pip install -r requirements.txt

# 환경변수
cp .env.example .env

# 체크포인트 복사 (학습 리포에서)
mkdir -p checkpoints/detector checkpoints/classifier
cp ../xbd_disaster_classifier/checkpoints/detector/best_ft.pth  checkpoints/detector/
cp ../xbd_disaster_classifier/checkpoints/classifier/best_ft.pth checkpoints/classifier/

# 실행
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 환경변수

```env
# 모델 체크포인트 (EBS PVC 마운트 경로)
DETECTOR_CKPT=/model/detector/best_ft.pth
CLASSIFIER_CKPT=/model/classifier/best_ft.pth

# 결과 전송 대상 (대시보드 서버)
DASHBOARD_URL=http://dashboard-service:8001/api/ingest

# 추론 디바이스
DEVICE=cuda
```

---

## 배포 (민영이 담당)

### 민영이에게 전달할 내용

```
GitHub 리포    : https://github.com/KWNahyun/core-vision-service
ECR 이미지명   : core-vision-service
포트           : 8000
GPU 필요       : nvidia.com/gpu: 1 (p3.xlarge)
EBS PVC        : /model/ (initContainer가 S3에서 pth 다운로드)
S3 모델 경로   : s3://my-mlops-prod-models/models/model.tar.gz
환경변수       : .env.example 참고
네임스페이스   : disaster-monitor (민규 서버와 동일)
Service 이름   : vision-service:8000
```

### 모델 파일 업로드 (학습 완료 후)

```bash
cd ~/xbd_disaster_classifier_vb

tar -czvf model.tar.gz \
  checkpoints/detector/best_ft.pth \
  checkpoints/classifier/best_ft.pth

aws s3 cp model.tar.gz s3://my-mlops-prod-models/models/model.tar.gz
```

---

## 관련 리포

| 리포 | 역할 |
|---|---|
| `xbd_disaster_classifier` | 학습 및 실험 |
| `core-vision-service` | 추론 서빙 (이 리포) |
| `core-dashboard` | 대시보드 프론트 + 백엔드 |
