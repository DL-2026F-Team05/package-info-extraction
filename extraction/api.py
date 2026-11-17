"""정보 추출 모듈을 혼자 쓰거나 다른 모듈과 연결할 때 부르는 함수.  담당: 이서영

    from extraction import extract
    info = extract(ocr_result, ["우유", "땅콩"], today="2026-11-17")
    info["risk"]["level"]      # danger | caution | unknown | ok
    info["priority"]           # 음성으로 읽을 순서

ocr_result 는 AI 2 run_pipeline 결과, OCR 줄 목록, AI 4 샘플 JSON, 글자 문자열 무엇이든 된다 (adapters.py).
돌려주는 dict 는 package-ocr-preprocessing/docs/interface.md 의 'extract() 출력 예시' 키를 그대로 포함하고,
AI 4 가 쓰는 Fields·Risk 전체도 "fields", "risk" 에 같이 넣는다.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from ._deps import KIND_LABEL, RISK_LABEL, STATUS_LABEL, Config, Fields, Risk, UserProfile, kst_today
from .adapters import OCRInput, to_input
from .allergens import HIGH_RISK, canonicalize_list
from .fields import extract_fields
from .risk import assess_risk


def _today(today: dt.date | str | None) -> dt.date:
    if isinstance(today, str):
        return dt.date.fromisoformat(today)
    return today or kst_today()


def _profile(user_allergens) -> UserProfile:
    if isinstance(user_allergens, UserProfile):
        return user_allergens
    if isinstance(user_allergens, str):
        user_allergens = [a for a in user_allergens.replace(",", " ").split() if a]
    return UserProfile(allergens=list(user_allergens or []))


def analyze(
    ocr_result: Any,
    user_allergens=None,
    today: dt.date | str | None = None,
    cfg: Config | None = None,
) -> tuple[Fields, Risk, OCRInput]:
    """AI 4 형식(Fields, Risk) 그대로 돌려준다. 파이프라인 밖에서 추출만 할 때."""
    inp = to_input(ocr_result)
    profile = _profile(user_allergens)
    fields = extract_fields(inp.boxes, profile, _today(today), cfg or Config())
    return fields, assess_risk(fields, profile), inp


def priority(fields: Fields, risk: Risk, has_cautions: bool = False) -> list[str]:
    """음성으로 읽을 순서. 값은 package-ocr-preprocessing docs/interface.md 3번의 키 이름
    ("allergens", "expiry", "product_name", "cautions")이고, 위험한 것부터 앞에 둔다.

    내 알레르기 함유 → 소비기한 지남 → 내 알레르기 혼입 가능 → 소비기한 → 제품명 → 알레르기 전체 → 주의사항
    (AI 4 안내 문장 speech.compose 와 같은 순서)
    """
    exp, alg = fields.expiry, fields.allergens
    order: list[str] = []

    def add(key: str) -> None:
        if key not in order:
            order.append(key)

    if alg.user_hits:
        add("allergens")
    if exp.status == "expired":
        add("expiry")
    if alg.user_may_hits:
        add("allergens")
    add("expiry")
    if fields.product_name.value:
        add("product_name")
    if alg.contains or alg.may_contain:
        add("allergens")
    if has_cautions:
        add("cautions")
    return order


def extract(
    ocr_result: Any,
    user_allergens=None,
    today: dt.date | str | None = None,
    cfg: Config | None = None,
) -> dict:
    """OCR 결과 → 핵심 정보 + 위험 판단 dict. 정보 추출 모듈의 대표 함수."""
    fields, risk, inp = analyze(ocr_result, user_allergens, today, cfg)
    exp, alg = fields.expiry, fields.allergens
    cautions = [r for r in alg.raw if r and not r.startswith("원재료명에서") and ("시설" in r or "혼입" in r or "contain" in r.lower())]
    if fields.caution.value:
        cautions.append(fields.caution.value)
    return {
        "image": inp.image,
        "source": inp.source,
        "product_name": fields.product_name.value,
        "product_name_conf": round(fields.product_name.conf, 4),
        "expiry": {
            "raw": exp.raw,
            "date": exp.date.isoformat() if exp.date else None,
            "kind": exp.kind,
            "kind_label": KIND_LABEL.get(exp.kind),
            "status": exp.status,
            "status_label": STATUS_LABEL.get(exp.status),
            "days_left": exp.days_left,
            "expired": None if exp.status in ("unknown", "elsewhere") else exp.status == "expired",
            "conf": round(exp.conf, 4),
        },
        "allergens": {
            "found": alg.contains,
            "may_contain": alg.may_contain,
            "warning": alg.user_hits,
            "warning_may": alg.user_may_hits,
            "high_risk": [a for a in alg.contains + alg.may_contain if a in HIGH_RISK],
            "user": canonicalize_list(_profile(user_allergens).allergens),
            "conf": round(alg.conf, 4),
        },
        "cautions": cautions,
        "risk": {"level": risk.level, "label": RISK_LABEL.get(risk.level), "reasons": risk.reasons},
        "priority": priority(fields, risk, bool(cautions)),
        "quality": inp.quality,
        "fields": fields.model_dump(mode="json"),
    }
