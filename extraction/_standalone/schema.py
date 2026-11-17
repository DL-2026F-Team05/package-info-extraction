"""AI 3 가 주고받는 결과 형식 (혼자 실행할 때 쓰는 사본).

AI 4 레포(AI_tts_web)의 packreader/schema.py 에서 정보 추출이 쓰는 부분만 같은 이름·같은 필드로 옮겨 두었다.
AI_tts_web 과 합쳐서 실행하면 extraction/_deps.py 가 이 파일 대신 packreader.schema 원본을 쓴다.
필드를 바꾸지 않는다 (바꾸면 합쳤을 때 AI 4 안내 문장이 깨진다).
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"

RegionName = Literal["product_name", "expiry", "allergen", "ingredients", "nutrition", "caution", "other"]
ExpiryKind = Literal["use_by", "sell_by", "best_before", "unknown"]
ExpiryStatus = Literal["ok", "soon", "expired", "elsewhere", "unknown"]
RiskLevel = Literal["danger", "caution", "ok", "unknown"]
QualityIssue = Literal["blur", "dark", "glare", "small"]

REGION_NAMES: tuple[str, ...] = (
    "product_name", "expiry", "allergen", "ingredients", "nutrition", "caution", "other",
)

# 화면과 안내 문장에 쓰는 한국어 이름
REGION_LABEL = {
    "product_name": "제품명",
    "expiry": "소비기한",
    "allergen": "알레르기",
    "ingredients": "원재료",
    "nutrition": "영양정보",
    "caution": "주의사항",
    "other": "기타",
}
KIND_LABEL = {"use_by": "소비기한", "sell_by": "유통기한", "best_before": "품질유지기한", "unknown": "날짜"}
STATUS_LABEL = {"ok": "남음", "soon": "곧 지남", "expired": "지남", "elsewhere": "다른 면에 표기", "unknown": "판단 보류"}
RISK_LABEL = {"danger": "위험", "caution": "주의", "ok": "특이사항 없음", "unknown": "확인 필요"}


class TextBox(BaseModel):
    """OCR 한 줄. 담당: 이도현 (ocr). 정답 라벨(labels/boxes/*.json)도 같은 형식을 쓴다."""

    text: str
    conf: float = Field(1.0, ge=0, le=1)
    poly: list[tuple[float, float]] | None = Field(None, description="네 꼭짓점 [[x, y], ...] (처리 이미지 픽셀)")
    region: RegionName | None = None


class TextField(BaseModel):
    value: str | None = None
    conf: float = Field(0.0, ge=0, le=1)


class Expiry(BaseModel):
    """소비기한·유통기한. 담당: 이서영 (extract)"""

    raw: str | None = Field(None, description="근거가 된 원문")
    date: dt.date | None = None
    kind: ExpiryKind = "unknown"
    status: ExpiryStatus = "unknown"
    days_left: int | None = None
    conf: float = Field(0.0, ge=0, le=1)


class Allergens(BaseModel):
    """알레르기 정보. 성분 이름은 extract.allergens.ALLERGENS 의 표준 이름만 쓴다. 담당: 이서영"""

    contains: list[str] = Field(default_factory=list, description="함유")
    may_contain: list[str] = Field(default_factory=list, description="같은 제조시설 등 혼입 가능")
    user_hits: list[str] = Field(default_factory=list, description="사용자 등록 성분 중 contains 에 있는 것")
    user_may_hits: list[str] = Field(default_factory=list, description="사용자 등록 성분 중 may_contain 에 있는 것")
    raw: list[str] = Field(default_factory=list, description="근거 문장")
    conf: float = Field(0.0, ge=0, le=1)


class Fields(BaseModel):
    product_name: TextField = Field(default_factory=TextField)
    expiry: Expiry = Field(default_factory=Expiry)
    allergens: Allergens = Field(default_factory=Allergens)
    ingredients: TextField = Field(default_factory=TextField)
    nutrition: TextField = Field(default_factory=TextField)
    caution: TextField = Field(default_factory=TextField)


class Risk(BaseModel):
    """위험 판단. 담당: 이서영 (extract.risk)"""

    level: RiskLevel = "unknown"
    reasons: list[str] = Field(
        default_factory=list,
        description="allergen_contains, expired, allergen_may, expiry_soon, expiry_unknown, expiry_elsewhere",
    )


class UserProfile(BaseModel):
    allergens: list[str] = Field(default_factory=list, description="사용자가 등록한 알레르기 성분")
