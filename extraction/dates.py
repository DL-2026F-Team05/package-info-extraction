"""날짜 찾기, 정규화, 소비기한 고르기.  담당: 이서영

처리하는 형식 (모두 YYYY-MM-DD 로 정규화)

| 형식 | 예 | 비고 |
|---|---|---|
| 연.월.일 | 2026.11.20 / 2026-11-20 / 2026/11/20 / 2026 . 11 . 20 | |
| 한글 | 2026년 11월 20일 / 26년 11월 20일 | |
| 두 자리 연도 | 26.11.20 | 신뢰도 조금 낮춤 |
| 일/월/연도 | 20/11/2026, 11/20/2026 | 수입품. 일·월을 가를 수 없으면(05/06/2026) 판단 보류 |
| 영어 월 | 20 NOV 2026, NOV 20, 2026 | 수입품 |
| 숫자만 | 20261120, 261120 | 앞에 기한 키워드(EXP, 소비기한 등)가 있을 때만 (로트번호·바코드 오탐 방지) |
| 연.월 | 2026.11, 2026년 11월 | 키워드가 있을 때만. 그 달 마지막 날로 본다 |
| 상대 표기 | 제조일로부터 12개월 | 제조일자 + 12개월 - 1일 |

OCR 이 숫자를 글자로 읽은 것(2O26.1l.2O)은 ocr.clean_ocr_text 로 고친 뒤 찾고, 고친 경우 신뢰도를 조금 낮춘다.
앞에 붙은 말로 종류를 가른다: 소비기한(EXP, USE BY), 유통기한, 품질유지기한(BEST BEFORE), 제조일자(소비기한 후보에서 뺌).
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
from dataclasses import dataclass, replace

from ._deps import clean_ocr_text

# 신뢰도 깎는 비율
PENALTY = {
    "ocr_fixed": 0.9,  # O→0 처럼 글자를 고쳐서 읽은 날짜
    "two_digit_year": 0.95,
    "compact": 0.9,  # 구분자 없는 숫자
    "month_only": 0.9,
    "ambiguous_dmy": 0.5,  # 일·월을 가를 수 없음 → 기본 기준(0.6) 아래로 떨어져 판단 보류
    "elsewhere_adopted": 0.9,  # '별도표기일까지' 문구 + 종류 모르는 날짜를 소비기한으로 본 경우
    "before_manufacture": 0.5,  # 소비기한이 제조일보다 앞섬 → 잘못 읽었을 가능성
    "implausible": 0.5,  # 오늘보다 3년 넘게 지났거나 10년 넘게 남음
    "conflicting_dates": 0.5,  # 같은 종류(예: 소비기한)인데 날짜가 서로 다름 → 하나를 잘못 읽었을 수 있음
    "relative": 0.95,
    "until_only": 0.95,  # 종류 키워드 없이 '○○까지'만 있는 날짜를 소비기한으로 본 경우
}

_MON = r"(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|SEPT|OCT|NOV|DEC)[A-Z]*\.?"
_MONTHS = {m: i for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), start=1)}
_SEP = r"\s*[.\-/·]\s*"

# (이름, 정규식, 키워드가 있어야만 쓰는지). 위에서부터 보고, 이미 잡힌 자리와 겹치면 건너뛴다.
_DATE_PATTERNS: tuple[tuple[str, re.Pattern, bool], ...] = (
    ("kor", re.compile(r"(?<!\d)(20\d{2}|\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일"), False),
    ("ymd", re.compile(rf"(?<!\d)(20\d{{2}}){_SEP}(\d{{1,2}}){_SEP}(\d{{1,2}})(?!\d)"), False),
    ("dmy", re.compile(r"(?<!\d)(\d{1,2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(20\d{2})(?!\d)"), False),
    ("d_mon_y", re.compile(rf"(?<!\d)(\d{{1,2}})\s*[.\-/ ]?\s*{_MON}\s*[.\-/ ]?\s*(20\d{{2}}|\d{{2}})(?!\d)", re.I), False),
    ("mon_d_y", re.compile(rf"(?<![A-Za-z]){_MON}\s*(\d{{1,2}}),?\s*(20\d{{2}})(?!\d)", re.I), False),
    ("ymd8", re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)"), True),
    ("ymd2", re.compile(rf"(?<![\d.])(\d{{2}}){_SEP}(\d{{1,2}}){_SEP}(\d{{1,2}})(?![\d])"), False),
    ("ym_kor", re.compile(r"(?<!\d)(20\d{2})\s*년\s*(\d{1,2})\s*월(?!\s*\d)"), True),
    ("ym", re.compile(rf"(?<!\d)(20\d{{2}}){_SEP}(\d{{1,2}})(?!\s*[.\-/·]?\s*\d)"), True),
    ("ymd6", re.compile(r"(?<!\d)(\d{2})(\d{2})(\d{2})(?!\d)"), True),
)

_KIND_PATTERNS = (
    ("use_by", re.compile(r"소\s*비\s*기\s*[한힌함]|\bEXP(?:IRY|IRATION)?\b|\bUSE\s*BY\b|\bE\s*/\s*D\b", re.I)),
    ("sell_by", re.compile(r"유\s*통\s*기\s*[한힌함]|\bSELL\s*BY\b", re.I)),
    ("best_before", re.compile(r"품\s*질\s*유\s*지\s*기\s*한|\bBEST\s*BEFORE\b|\bBBE?\b", re.I)),
    ("manufactured", re.compile(r"제\s*조\s*일|제\s*조\s*연\s*월\s*일|포\s*장\s*일|생\s*산\s*일|\bMFD\b|\bMFG\b|\bPROD(?:UCTION)?\b", re.I)),
)
# '별도표기일까지', '후면 표기일까지', '윗면 표시일까지' (EasyOCR 은 '표'를 '포'로 읽기도 한다)
_ELSEWHERE = re.compile(
    r"별\s*도\s*(?:표\s*기|표\s*시)|[표포]\s*기\s*일\s*까\s*지|표\s*시\s*일\s*까\s*지|"
    r"(?:윗|아랫|옆|측|뒷|후|밑|바닥|뚜껑|용기|상단|하단)\s*면?\s*(?:에\s*)?(?:표기|표시|참조)"
)
_RELATIVE = re.compile(r"제\s*조\s*일\s*(?:자)?\s*(?:로|으로)?\s*부\s*터\s*(\d{1,3})\s*(개월|달|일|년)")
_UNTIL = re.compile(r"^\s*(?:까지|까\s*지|이전|前)")
_KIND_PRIORITY = {"use_by": 0, "sell_by": 1, "best_before": 2, "unknown": 3}
_MIN, _MAX = dt.date(2000, 1, 1), dt.date(2045, 12, 31)


@dataclass
class DateCandidate:
    date: dt.date
    raw: str
    kind: str  # use_by | sell_by | best_before | manufactured | unknown
    line: int
    conf: float
    fmt: str = "ymd"
    granularity: str = "day"  # day | month (월까지만 표기 → 그 달 마지막 날)
    until: bool = False  # 바로 뒤에 '까지'가 붙었는지 (소비기한일 가능성이 높다)
    notes: tuple[str, ...] = ()

    def penalize(self, reason: str) -> "DateCandidate":
        return replace(self, conf=self.conf * PENALTY[reason], notes=self.notes + (reason,))


def kind_of(text: str) -> str | None:
    """글자 안에서 가장 마지막에 나온 날짜 종류 키워드."""
    best, best_pos = None, -1
    for kind, rx in _KIND_PATTERNS:
        for m in rx.finditer(text):
            if m.start() > best_pos:
                best, best_pos = kind, m.start()
    return best


def _make_date(y: int, m: int, d: int | None) -> tuple[dt.date, str] | None:
    if y < 100:
        y += 2000
    if not 1 <= m <= 12:
        return None
    if d is None:
        day, gran = calendar.monthrange(y, m)[1], "month"
    else:
        day, gran = d, "day"
    try:
        out = dt.date(y, m, day)
    except ValueError:
        return None
    return (out, gran) if _MIN <= out <= _MAX else None


def parse_dates(text: str, keyword: bool = True) -> list[tuple[int, int, dt.date, str, str, bool]]:
    """글자 한 줄에서 날짜를 모두 찾는다. [(시작, 끝, 날짜, 형식, 단위, 일·월 애매)]

    keyword=False 이면 숫자만 있는 형식(20261120, 2026.11)은 찾지 않는다.
    """
    found: list[tuple[int, int, dt.date, str, str, bool]] = []
    for fmt, rx, needs_kw in _DATE_PATTERNS:
        if needs_kw and not keyword:
            continue
        for m in rx.finditer(text):
            if any(m.start() < e and s < m.end() for s, e, *_ in found):
                continue
            g = m.groups()
            ambiguous = False
            if fmt in ("kor", "ymd", "ymd8", "ymd2", "ymd6"):
                r = _make_date(int(g[0]), int(g[1]), int(g[2]))
            elif fmt == "dmy":
                a, b, y = int(g[0]), int(g[1]), int(g[2])
                if a > 12:
                    r = _make_date(y, b, a)  # 일/월/연도 (유럽식)
                elif b > 12:
                    r = _make_date(y, a, b)  # 월/일/연도 (미국식)
                else:
                    r, ambiguous = _make_date(y, b, a), a != b
            elif fmt == "d_mon_y":
                r = _make_date(int(g[2]), _MONTHS[g[1].upper()[:3]], int(g[0]))
            elif fmt == "mon_d_y":
                r = _make_date(int(g[2]), _MONTHS[g[0].upper()[:3]], int(g[1]))
            else:  # ym_kor, ym
                r = _make_date(int(g[0]), int(g[1]), None)
            if r is None:
                continue
            found.append((m.start(), m.end(), r[0], fmt, r[1], ambiguous))
    return sorted(found)


def _has_date(text: str) -> bool:
    return bool(parse_dates(clean_ocr_text(text), keyword=False))


def find_dates(lines: list[tuple[str, float]]) -> list[DateCandidate]:
    """lines: [(글자, 신뢰도), ...] 읽는 순서대로. 박스 하나 = 한 줄로 넣는다."""
    out: list[DateCandidate] = []
    for i, (orig, conf) in enumerate(lines):
        text = clean_ocr_text(orig)
        fixed = text != orig.strip()
        # 키워드가 이 줄에 있거나, 바로 앞의 짧은 줄(표의 '소비기한' 칸)에 있는지
        prev_kind = None
        if i > 0:
            prev = lines[i - 1][0]
            if len(prev.strip()) <= 15 and not _has_date(prev):
                prev_kind = kind_of(prev)
        has_kw = kind_of(text) is not None or prev_kind is not None
        for s, e, day, fmt, gran, ambiguous in parse_dates(text, keyword=has_kw):
            kind = kind_of(text[:s]) or prev_kind or "unknown"
            c = DateCandidate(
                date=day, raw=text[s:e].strip(), kind=kind, line=i, conf=float(conf), fmt=fmt,
                granularity=gran, until=bool(_UNTIL.match(text[e:])),
            )
            if fixed and text[s:e] not in orig:
                c = c.penalize("ocr_fixed")
            if fmt in ("ymd2", "ymd6") or (fmt == "kor" and re.match(r"\d{2}\s*년", text[s:e])):
                c = c.penalize("two_digit_year")
            if fmt in ("ymd8", "ymd6"):
                c = c.penalize("compact")
            if gran == "month":
                c = c.penalize("month_only")
            if ambiguous:
                c = c.penalize("ambiguous_dmy")
            out.append(c)
    return out


def pick_expiry(cands: list[DateCandidate]) -> DateCandidate | None:
    """소비기한 > 유통기한 > 품질유지기한 > 종류 모름('까지'가 붙은 것 먼저) 순서.

    같은 순위면 신뢰도 높은 것, 그다음 늦은 날짜 (제조일과 소비기한이 이름 없이 같이 찍힌 경우 대비).
    """
    pool = [c for c in cands if c.kind != "manufactured"]
    if not pool:
        return None

    def key(c: DateCandidate):
        prio = _KIND_PRIORITY.get(c.kind, 9) + (0 if c.kind != "unknown" or c.until else 0.5)
        return (prio, -round(c.conf, 3), -c.date.toordinal())

    return min(pool, key=key)


def find_elsewhere(lines: list[tuple[str, float]]) -> tuple[str, float] | None:
    """'소비기한: 별도표기일까지', '후면표기일까지' 같은 줄을 찾는다. (원문, 신뢰도)"""
    for i, (text, conf) in enumerate(lines):
        if _ELSEWHERE.search(text):
            raw = text.strip()
            # 표의 '소비기한' 칸과 '후면표기일까지' 칸이 따로 읽힌 경우 앞 칸의 이름을 붙여 둔다
            if kind_of(raw) is None and i > 0 and kind_of(lines[i - 1][0]) and len(lines[i - 1][0]) <= 15:
                raw = f"{lines[i - 1][0].strip()} {raw}"
            return raw, float(conf)
    return None


@dataclass
class RelativeExpiry:
    amount: int
    unit: str  # 개월 | 달 | 일 | 년
    raw: str
    kind: str
    conf: float


def find_relative(lines: list[tuple[str, float]]) -> RelativeExpiry | None:
    """'소비기한: 제조일로부터 12개월' 같은 상대 표기."""
    for i, (text, conf) in enumerate(lines):
        m = _RELATIVE.search(text)
        if not m:
            continue
        kind = kind_of(text[: m.start()])
        if kind in (None, "manufactured") and i > 0:
            kind = kind_of(lines[i - 1][0])
        kind = kind if kind in ("use_by", "sell_by", "best_before") else "use_by"
        return RelativeExpiry(int(m.group(1)), m.group(2), text.strip(), kind, float(conf))
    return None


def add_period(base: dt.date, amount: int, unit: str) -> dt.date:
    """제조일 + 기간 - 1일. 2026-05-20 + 12개월 → 2027-05-19 (제조일 당일을 1일째로 센다)."""
    if unit in ("개월", "달"):
        months = base.month - 1 + amount
        y, m = base.year + months // 12, months % 12 + 1
        end = dt.date(y, m, min(base.day, calendar.monthrange(y, m)[1]))
    elif unit == "년":
        y = base.year + amount
        end = dt.date(y, base.month, min(base.day, calendar.monthrange(y, base.month)[1]))
    else:
        end = base + dt.timedelta(days=amount)
    return end - dt.timedelta(days=1)


@dataclass
class ExpiryChoice:
    cand: DateCandidate | None  # 고른 날짜
    elsewhere: tuple[str, float] | None  # '별도표기일까지' 문구 (날짜를 못 고른 경우)
    pending: tuple[str, float] | None = None  # 기한 문구는 있는데 날짜를 계산 못 함 (제조일로부터 n개월 + 제조일 없음)


def choose_expiry(lines: list[tuple[str, float]], today: dt.date) -> ExpiryChoice:
    """줄 목록에서 소비기한 하나를 고른다.

    1. 종류가 적힌 날짜 → 그중 우선순위대로
    2. 날짜가 없고 '제조일로부터 n개월'이면 제조일자로 계산
    3. '별도표기일까지' 문구가 있고 종류 모르는 날짜가 따로 찍혀 있으면 그 날짜를 소비기한으로 본다 (신뢰도 깎음)
       종류 키워드 없이 '2026.12.15까지'처럼 '까지'가 붙은 날짜도 소비기한으로 본다
    4. 앞뒤가 안 맞는 날짜(제조일보다 이른 소비기한, 너무 먼 날짜)는 신뢰도를 깎아 판단을 보류하게 한다
    """
    cands = find_dates(lines)
    elsewhere = find_elsewhere(lines)
    mfg = sorted((c for c in cands if c.kind == "manufactured"), key=lambda c: -c.conf)
    best = pick_expiry(cands)

    if best is None or best.kind == "unknown":
        rel = find_relative(lines)
        if rel and mfg:
            base = mfg[0]
            day = add_period(base.date, rel.amount, rel.unit)
            best = DateCandidate(
                date=day, raw=rel.raw, kind=rel.kind, line=-1, conf=min(rel.conf, base.conf), fmt="relative",
            ).penalize("relative")
        elif rel and best is None:
            # 제조일자를 못 읽었으면 날짜는 모르지만 '판단 보류'로 남긴다
            return ExpiryChoice(None, None, (rel.raw, rel.conf))

    if best is not None and best.kind == "unknown" and elsewhere:
        unknowns = [c for c in cands if c.kind == "unknown"]
        if best.until or len(unknowns) == 1:
            ek = kind_of(elsewhere[0])
            best = replace(best, kind=ek if ek in _KIND_PRIORITY and ek != "unknown" else "use_by")
            best = best.penalize("elsewhere_adopted")
            elsewhere = None

    if best is not None and best.kind == "unknown" and best.until:
        # 날짜 바로 뒤의 '까지'는 기한 표시다 (제조일자에는 붙지 않는다). 2024년부터 식품은 소비기한 표시
        best = replace(best, kind="use_by").penalize("until_only")

    if best is not None and best.kind != "unknown":
        # 같은 종류 기한이 서로 다른 날짜로 두 번 이상 읽힘 (앞·뒤에 찍힌 날짜 중 하나를 잘못 읽은 경우 등)
        others = {c.date for c in cands if c.kind == best.kind and c.date != best.date and c.conf >= 0.5}
        if others:
            best = best.penalize("conflicting_dates")

    if best is not None:
        if mfg and best.date < mfg[0].date:
            best = best.penalize("before_manufacture")
        if (best.date - today).days < -3 * 365 or (best.date - today).days > 10 * 365:
            best = best.penalize("implausible")
    return ExpiryChoice(best, None if best is not None else elsewhere)
