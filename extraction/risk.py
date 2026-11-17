"""소비기한 상태, 사용자 알레르기 맞추기, 위험 판단.  담당: 이서영

판단 보류 원칙: 신뢰도가 낮으면 '괜찮다'고 말하지 않고 status=unknown 으로 둔다.
가장 위험한 실패는 날짜를 잘못 읽고 "아직 괜찮아요"라고 말하는 것이다.

위험 단계

| level | 조건 (reasons) |
|---|---|
| danger | 내 알레르기 성분 함유(allergen_contains), 소비기한 지남(expired) |
| caution | 내 알레르기 성분 혼입 가능(allergen_may), 기한 3일 이하(expiry_soon) |
| unknown | 기한을 못 읽음·판단 보류(expiry_unknown), 다른 면에 표기(expiry_elsewhere), 알레르기를 등록했는데 표시를 못 찾음(allergen_unknown) |
| ok | 위에 해당 없음 |

내 알레르기 성분이 흐린 글자에서 오탈자로 맞춰진 경우(allergen_uncertain)도 함유로 본다. 놓치는 것보다 한 번 더 확인하게 하는 쪽이 안전하다.
"""

from __future__ import annotations

import datetime as dt

from ._deps import Config
from ._deps import Allergens, Expiry, Fields, Risk, UserProfile
from .allergens import canonicalize_list
from .dates import DateCandidate, kind_of

_KINDS = ("use_by", "sell_by", "best_before")
_DANGER = ("allergen_contains", "expired")
_CAUTION = ("allergen_may", "expiry_soon")
_UNKNOWN = ("expiry_unknown", "expiry_elsewhere", "allergen_unknown")


def build_expiry(
    cand: DateCandidate | None,
    elsewhere: tuple[str, float] | None,
    today: dt.date,
    cfg: Config,
    pending: tuple[str, float] | None = None,
) -> Expiry:
    if cand is None:
        if elsewhere:
            raw, conf = elsewhere
            kind = kind_of(raw)
            return Expiry(raw=raw, kind=kind if kind in _KINDS else "use_by", status="elsewhere", conf=conf)
        if pending:
            raw, conf = pending
            kind = kind_of(raw)
            return Expiry(raw=raw, kind=kind if kind in _KINDS else "use_by", status="unknown", conf=conf)
        return Expiry()
    kind = cand.kind if cand.kind in _KINDS else "unknown"
    conf = max(0.0, min(1.0, cand.conf))
    if conf < cfg.expiry_min_conf:
        # 날짜는 남겨 두되(평가·디버깅용) 판단은 보류
        return Expiry(raw=cand.raw, date=cand.date, kind=kind, status="unknown", conf=conf)
    days = (cand.date - today).days
    status = "expired" if days < 0 else ("soon" if days <= cfg.soon_days else "ok")
    return Expiry(raw=cand.raw, date=cand.date, kind=kind, status=status, days_left=days, conf=conf)


def match_profile(alg: Allergens, profile: UserProfile | None) -> Allergens:
    mine = set(canonicalize_list(profile.allergens)) if profile else set()
    return alg.model_copy(
        update={
            "user_hits": [a for a in alg.contains if a in mine],
            "user_may_hits": [a for a in alg.may_contain if a in mine],
        }
    )


def assess_risk(fields: Fields, profile: UserProfile | None = None) -> Risk:
    """profile 을 주면 '알레르기를 등록했는데 표시를 못 찾은 경우'도 판단한다 (없으면 기존과 같다)."""
    exp, alg = fields.expiry, fields.allergens
    reasons: list[str] = []
    if alg.user_hits:
        reasons.append("allergen_contains")
    if exp.status == "expired":
        reasons.append("expired")
    if alg.user_may_hits:
        reasons.append("allergen_may")
    if exp.status == "soon":
        reasons.append("expiry_soon")
    if exp.status == "unknown":
        reasons.append("expiry_unknown")
    if exp.status == "elsewhere":
        reasons.append("expiry_elsewhere")
    has_profile = bool(profile and canonicalize_list(profile.allergens))
    if has_profile and not alg.contains and not alg.may_contain:
        # 알레르기 표시를 못 읽었는데 '특이사항 없음'이라고 하면 위험을 놓친다
        reasons.append("allergen_unknown")

    if any(r in _DANGER for r in reasons):
        level = "danger"
    elif any(r in _CAUTION for r in reasons):
        level = "caution"
    elif any(r in _UNKNOWN for r in reasons):
        level = "unknown"
    else:
        level = "ok"
    return Risk(level=level, reasons=reasons)
