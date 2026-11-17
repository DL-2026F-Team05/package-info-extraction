"""AI 3 · 핵심 정보 추출, 날짜 해석, 알레르겐 매칭, 위험 판단.  담당: 이서영

다른 모듈에서 (package-ocr-preprocessing docs/interface.md 3번과 같은 모양):
    from extraction import extract
    info = extract(result["ocr"], ["우유", "땅콩"])      # result = AI 2 run_pipeline 결과

AI 4 파이프라인 형식 (Fields, Risk):
    from extraction import extract_fields, assess_risk
    fields = extract_fields(boxes, profile, today, cfg)
    risk = assess_risk(fields)

명령줄:   python -m extraction data/samples/ai2_week2 --allergy 우유 땅콩
연결 확인: python -m extraction.demo
평가:     python -m extraction.evaluate --ocr-dir data/samples/ai2_week2 --labels data/labels/labels.csv
"""

from ._deps import INTEGRATED
from .adapters import OCRInput, load, to_boxes, to_input
from .allergens import (
    ALLERGENS, HIGH_RISK, canonical, canonicalize_list, find_allergens, find_allergens_ocr, ingredient_allergens,
)
from .api import analyze, extract, priority
from .fields import extract_fields
from .risk import assess_risk, build_expiry, match_profile

__all__ = [
    "ALLERGENS", "HIGH_RISK", "canonical", "canonicalize_list", "find_allergens", "find_allergens_ocr",
    "ingredient_allergens", "extract_fields", "assess_risk", "build_expiry", "match_profile",
    "extract", "analyze", "priority", "OCRInput", "load", "to_boxes", "to_input", "INTEGRATED",
]
