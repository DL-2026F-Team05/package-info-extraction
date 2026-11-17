"""다른 모듈과 같이 쓰는 형식·도우미를 한곳에서 가져온다.

- AI_tts_web(packreader)과 합쳐서 실행하면: packreader 원본 클래스·함수를 쓴다.
  → extract_fields 가 돌려주는 Fields 가 AI 4 안내 문장(speech.compose)에 그대로 들어간다.
- 이 레포만 있으면: _standalone/ 의 사본을 쓴다 (같은 이름·같은 필드).
"""

from __future__ import annotations

try:
    from packreader.config import Config, kst_today
    from packreader.ocr.base import box_height, clean_ocr_text, group_lines, poly_bbox
    from packreader.schema import (
        KIND_LABEL, REGION_NAMES, RISK_LABEL, STATUS_LABEL,
        Allergens, Expiry, Fields, Risk, TextBox, TextField, UserProfile,
    )

    INTEGRATED = True
except ImportError:
    from ._standalone.config import Config, kst_today
    from ._standalone.schema import (
        KIND_LABEL, REGION_NAMES, RISK_LABEL, STATUS_LABEL,
        Allergens, Expiry, Fields, Risk, TextBox, TextField, UserProfile,
    )
    from ._standalone.text import box_height, clean_ocr_text, group_lines, poly_bbox

    INTEGRATED = False

__all__ = [
    "Config", "kst_today", "box_height", "clean_ocr_text", "group_lines", "poly_bbox",
    "KIND_LABEL", "REGION_NAMES", "RISK_LABEL", "STATUS_LABEL",
    "Allergens", "Expiry", "Fields", "Risk", "TextBox", "TextField", "UserProfile", "INTEGRATED",
]
