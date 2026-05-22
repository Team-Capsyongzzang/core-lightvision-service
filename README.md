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
