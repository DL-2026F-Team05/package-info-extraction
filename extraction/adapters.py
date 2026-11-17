"""다른 모듈의 OCR 결과를 TextBox 목록으로 바꾸기.  담당: 이서영

팀 안에서 OCR 결과 모양이 세 가지라서, 무엇을 넣어도 extract 가 받을 수 있게 한다.

| 어디서 | 모양 | 예 |
|---|---|---|
| AI 1·AI 2 (package-ocr-preprocessing `run_pipeline`) | `{"ocr": [{"text", "box": [[x,y]×4], "score"}], "quality": {...}}` | results/ai2_week2/*/sample_output/*.json |
| food-package-ocr README 약속 | `{"image", "lines": [{"text", "box": [x1,y1,x2,y2], "confidence"}]}` | |
| AI 4 (AI_tts_web) | `{"boxes": [{"text", "conf", "poly", "region"}]}` 또는 `list[TextBox]` | AI_tts_web data/samples/*.json |
| OCR 엔진 원본 | EasyOCR `[(poly, text, conf)]`, PaddleOCR `[[poly, (text, conf)]]` | |
| 글자만 | `"소비기한 2026.12.31까지\n우유 함유"` (줄바꿈 = 한 줄) | |
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._deps import REGION_NAMES, TextBox

_TEXT_KEYS = ("text", "transcription", "rec_text", "label")
_CONF_KEYS = ("conf", "score", "confidence", "prob", "rec_score")
_POLY_KEYS = ("poly", "box", "bbox", "points", "dt_poly", "polygon")
_LIST_KEYS = ("boxes", "ocr", "lines", "results", "result")


@dataclass
class OCRInput:
    """extract 에 넣을 것: 글자 박스 + 함께 온 정보(사진 이름, AI 2 품질 진단)."""

    boxes: list[TextBox]
    image: str | None = None
    quality: dict | None = None  # AI 2 run_pipeline 의 quality (retake, advice, issues, ocr_confidence)
    source: str = "unknown"  # ai2 | food-package-ocr | ai4 | raw | text
    extra: dict = field(default_factory=dict)


def _to_poly(v: Any) -> list[tuple[float, float]] | None:
    if v is None:
        return None
    try:
        if len(v) == 4 and all(isinstance(x, (int, float)) for x in v):  # [x1, y1, x2, y2]
            x1, y1, x2, y2 = (float(x) for x in v)
            return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
        pts = [(float(p[0]), float(p[1])) for p in v]
        return pts if len(pts) >= 3 else None
    except (TypeError, ValueError, IndexError):
        return None


def _clip(c: Any) -> float:
    try:
        c = float(c)
    except (TypeError, ValueError):
        return 1.0
    return 0.0 if c != c else max(0.0, min(1.0, c))  # NaN → 0


def _item_to_box(item: Any) -> TextBox | None:
    if isinstance(item, TextBox):
        return item
    if isinstance(item, str):
        return TextBox(text=item, conf=1.0) if item.strip() else None
    if isinstance(item, dict):
        text = next((item[k] for k in _TEXT_KEYS if isinstance(item.get(k), str)), None)
        if text is None:
            return None
        conf = next((item[k] for k in _CONF_KEYS if item.get(k) is not None), 1.0)
        poly = next((_to_poly(item[k]) for k in _POLY_KEYS if item.get(k) is not None), None)
        region = item.get("region")
        return TextBox(text=text, conf=_clip(conf), poly=poly, region=region if region in REGION_NAMES else None)
    if isinstance(item, (list, tuple)):
        # EasyOCR: (poly, text, conf)
        if len(item) == 3 and isinstance(item[1], str):
            return TextBox(text=item[1], conf=_clip(item[2]), poly=_to_poly(item[0]))
        # PaddleOCR: [poly, (text, conf)]
        if len(item) == 2 and isinstance(item[1], (list, tuple)) and len(item[1]) == 2 and isinstance(item[1][0], str):
            return TextBox(text=item[1][0], conf=_clip(item[1][1]), poly=_to_poly(item[0]))
    return None


def _flatten_paddle(data: list) -> list:
    """PaddleOCR 은 사진별로 한 번 더 감싼 [[...]] 를 돌려주기도 한다."""
    if len(data) == 1 and isinstance(data[0], list) and data[0] and isinstance(data[0][0], (list, tuple)) \
            and len(data[0][0]) == 2 and isinstance(data[0][0][1], (list, tuple)):
        return data[0]
    return data


def to_input(data: Any) -> OCRInput:
    """무엇이든 OCRInput 으로. 모르는 모양이면 ValueError."""
    if isinstance(data, OCRInput):
        return data
    if isinstance(data, str):
        return OCRInput([TextBox(text=t.strip(), conf=1.0) for t in data.splitlines() if t.strip()], source="text")
    if isinstance(data, dict):
        key = next((k for k in _LIST_KEYS if isinstance(data.get(k), list)), None)
        if key is None:
            raise ValueError(f"OCR 결과 목록을 찾지 못했어요. 키: {sorted(data)}")
        src = {"ocr": "ai2", "lines": "food-package-ocr", "boxes": "ai4"}.get(key, "raw")
        inp = to_input(data[key])
        q = data.get("quality") if isinstance(data.get("quality"), dict) else None
        image = data.get("image") if isinstance(data.get("image"), str) else None
        extra = {k: data[k] for k in ("engine", "preprocess", "elapsed_sec") if k in data}
        return OCRInput(inp.boxes, image=image, quality=q, source=src, extra=extra)
    if isinstance(data, (list, tuple)):
        boxes = [b for b in (_item_to_box(x) for x in _flatten_paddle(list(data))) if b is not None]
        if data and not boxes:
            raise ValueError("OCR 결과의 줄 모양을 알 수 없어요.")
        return OCRInput(boxes, source="raw")
    raise ValueError(f"OCR 결과로 쓸 수 없는 형식: {type(data).__name__}")


def to_boxes(data: Any) -> list[TextBox]:
    return to_input(data).boxes


def load(path: str | Path) -> OCRInput:
    """JSON 파일(.json) 또는 글자 파일(.txt)을 읽는다."""
    p = Path(path)
    text = p.read_text(encoding="utf-8-sig")
    if p.suffix.lower() == ".json":
        inp = to_input(json.loads(text))
    else:
        inp = to_input(text)
    if inp.image is None:
        inp.image = p.name
    return inp
