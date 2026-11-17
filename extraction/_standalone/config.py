"""정보 추출 기준값 (혼자 실행할 때 쓰는 사본).

AI_tts_web 의 packreader/config.py Config 중 정보 추출이 쓰는 값만 같은 이름·같은 기본값으로 옮겨 두었다.
AI_tts_web 과 합쳐서 실행하면 원본 Config 를 쓴다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

KST = dt.timezone(dt.timedelta(hours=9))


def kst_today() -> dt.date:
    """한국 날짜. 코랩 서버는 UTC 라서 date.today() 를 쓰면 아침 9시 전에는 하루 전 날짜가 나온다."""
    return dt.datetime.now(KST).date()


@dataclass
class Config:
    soon_days: int = 3  # 남은 날이 이 값 이하면 '곧 지남'
    expiry_min_conf: float = 0.6  # 이보다 낮으면 소비기한 판단을 보류한다
