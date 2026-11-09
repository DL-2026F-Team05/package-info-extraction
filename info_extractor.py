"""
AI 3 - 핵심 정보 추출 / 날짜 해석 / 알레르겐 매칭 / 위험 판단 모듈

입력 : OCR 결과 (문자열 또는 [{"text":..., "bbox":[x1,y1,x2,y2], "conf":0.0~1.0}, ...])
출력 : 카테고리별 구조화 정보 + 위험 판단 + 음성 안내 문장(TTS용) 딕셔너리

사용 예)
    from info_extractor import InfoExtractor
    ex = InfoExtractor("allergens.json")
    result = ex.extract(ocr_lines, user_allergies=["땅콩"], today="2026-11-05")
    print(result["tts_script"])
"""
from __future__ import annotations

import calendar
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

# ----------------------------------------------------------------------------
# 1. 카테고리 정의
# ----------------------------------------------------------------------------
CATEGORIES = {
    # code: (우선등급, 음성 이름)
    "EXPIRY":        (1, "소비기한"),
    "ALLERGEN":      (1, "알레르기 성분"),
    "CROSS_CONTACT": (1, "혼입 가능 성분"),
    "PRODUCT_NAME":  (2, "제품명"),
    "CAUTION":       (2, "주의사항"),
    "MFG_DATE":      (3, "제조일자"),
    "STORAGE":       (3, "보관방법"),
    "INGREDIENTS":   (3, "원재료명"),
    "NUTRITION":     (3, "내용량·영양정보"),
    "MANUFACTURER":  (3, "제조원"),
}

# 카테고리를 알려주는 앵커 키워드 (정규식). 위에서부터 먼저 검사한다.
ANCHORS: list[tuple[str, str]] = [
    ("EXPIRY",       r"소\s*비\s*기\s*한|유\s*통\s*기\s*한|품질\s*유지\s*기한|EXP(?:IRY|IRATION)?\b\.?|BEST\s*BEFORE|USE\s*BY|\bBB\b"),
    ("MFG_DATE",     r"제조\s*일\s*자|제조\s*연월일|제조\s*일(?!\s*로)|생산\s*일\s*자|\bMFD\b|\bMFG\b|PROD(?:UCTION)?\s*DATE"),
    ("ALLERGEN",     r"알레르기\s*(?:유발\s*)?(?:물질|성분|표시|정보)?|알러지"),
    ("INGREDIENTS",  r"원\s*재\s*료\s*명?(?:\s*및\s*함량)?|원료\s*명|INGREDIENTS?"),
    ("PRODUCT_NAME", r"제\s*품\s*명|품\s*명(?=\s*[:：])"),
    ("MANUFACTURER", r"제조원|판매원|제조사|수입원|유통전문판매원|제조\s*업소|소재지"),
    ("STORAGE",      r"보관\s*방법|냉장\s*보관|냉동\s*보관|실온\s*보관|보관\s*하"),
    ("NUTRITION",    r"영양\s*정보|영양\s*성분|총?\s*내용량|열량|kcal|나트륨|탄수화물"),
    ("CAUTION",      r"주의\s*사항|섭취\s*시|주의|경고|금지"),
]
CROSS_CONTACT_RE = re.compile(r"같은\s*(?:제조\s*)?시설|동일\s*(?:제조\s*)?시설|혼입\s*(?:될|가능)|함께\s*제조")
CONTAINS_RE = re.compile(r"함유|포함")
# 날짜가 다른 면에 있다는 표기 ("윗면 표기일까지" 등)
DATE_ELSEWHERE_RE = re.compile(r"(?:윗|아랫|옆|측|뒷|밑|상단|하단|별도|용기|뚜껑)\s*(?:면|부분)?\s*(?:에\s*)?(?:별도\s*)?표기|별도\s*표기")
# 계속 이어지는 줄을 받아줄 카테고리 (원재료명이 여러 줄에 걸치는 경우 등)
CONTINUABLE = {"INGREDIENTS", "ALLERGEN", "CAUTION", "STORAGE", "NUTRITION", "MANUFACTURER", "CROSS_CONTACT"}

# ----------------------------------------------------------------------------
# 2. 날짜 표기 사전
# ----------------------------------------------------------------------------
MONTHS_EN = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}
_MON = r"(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*\.?"
_SEP = r"\s*[.\-/·]\s*"

# (이름, 정규식, 앵커 근처에서만 허용 여부)  - 위에서부터 우선 적용, 겹치는 구간은 건너뜀
DATE_PATTERNS: list[tuple[str, re.Pattern, bool]] = [
    ("YMD_KOR",  re.compile(r"(20\d{2}|\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일"), False),
    ("YMD4",     re.compile(rf"(?<!\d)(20\d{{2}}){_SEP}(\d{{1,2}}){_SEP}(\d{{1,2}})(?!\d)"), False),
    ("DMY4",     re.compile(rf"(?<!\d)(\d{{1,2}}){_SEP}(\d{{1,2}}){_SEP}(20\d{{2}})(?!\d)"), False),
    ("D_MON_Y",  re.compile(rf"(?<!\d)(\d{{1,2}})\s*{_MON}\s*(20\d{{2}}|\d{{2}})(?!\d)", re.I), False),
    ("MON_D_Y",  re.compile(rf"{_MON}\s*(\d{{1,2}}),?\s*(20\d{{2}})(?!\d)", re.I), False),
    ("YMD8",     re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)"), True),
    ("YMD2",     re.compile(rf"(?<!\d)(\d{{2}}){_SEP}(\d{{1,2}}){_SEP}(\d{{1,2}})(?!\d)"), False),
    ("YM_KOR",   re.compile(r"(20\d{2})\s*년\s*(\d{1,2})\s*월(?!\s*\d)"), False),
    ("YM4",      re.compile(rf"(?<!\d)(20\d{{2}}){_SEP}(\d{{1,2}})(?!\s*[.\-/·]?\s*\d)"), True),
    ("YMD6",     re.compile(r"(?<!\d)(\d{2})(\d{2})(\d{2})(?!\d)"), True),
]
RELATIVE_RE = re.compile(r"제조\s*일\s*(?:자)?\s*로\s*부터\s*(\d+)\s*(개월|달|일|년)")

# OCR이 숫자를 문자로 잘못 읽는 경우 (숫자 덩어리 안에서만 교정)
OCR_DIGIT_FIX = str.maketrans({"O": "0", "o": "0", "D": "0", "I": "1", "l": "1", "|": "1", "!": "1",
                               "Z": "2", "S": "5", "B": "8", "g": "9"})
DIGITISH_RUN = re.compile(r"[0-9OoDIl|!ZSBg][0-9OoDIl|!ZSBg.\-/· ]{5,}[0-9OoDIl|!ZSBg]")


# ----------------------------------------------------------------------------
# 자료구조
# ----------------------------------------------------------------------------
@dataclass
class Line:
    text: str
    bbox: list[float] | None = None
    conf: float = 1.0
    idx: int = 0

    @property
    def height(self) -> float:
        return (self.bbox[3] - self.bbox[1]) if self.bbox else 0.0


@dataclass
class Segment:
    category: str | None
    text: str
    line: Line
    anchor: str = ""


@dataclass
class DateHit:
    value: date
    raw: str
    fmt: str
    granularity: str = "day"     # day | month
    ambiguous: bool = False
    conf: float = 1.0


@dataclass
class AllergenHit:
    id: str
    label: str
    status: str                  # CONTAINS | MAY_CONTAIN | INFERRED
    matched: str
    fuzzy: bool = False
    high_risk: bool = False
    conf: float = 1.0


# ----------------------------------------------------------------------------
# 유틸
# ----------------------------------------------------------------------------
def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("：", ":").replace("ㆍ", "·")
    text = re.sub(r"[ \t]+", " ", text).strip()

    def _fix(m: re.Match) -> str:
        s = m.group(0)
        if sum(c.isdigit() for c in s) >= 4:          # 실제 숫자가 4개 이상인 덩어리만 교정
            return s.translate(OCR_DIGIT_FIX)
        return s
    return DIGITISH_RUN.sub(_fix, text)


_CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_JONG = " ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"


def to_jamo(s: str) -> str:
    out = []
    for ch in s:
        code = ord(ch) - 0xAC00
        if 0 <= code < 11172:
            out += [_CHO[code // 588], _JUNG[(code % 588) // 28]]
            if code % 28:
                out.append(_JONG[code % 28])
        else:
            out.append(ch)
    return "".join(out)


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def safe_date(y: int, m: int, d: int | None) -> tuple[date, str] | None:
    if y < 100:
        y += 2000
    if not (1 <= m <= 12):
        return None
    if d is None:                                     # 월까지만 표기 -> 그 달 말일
        return date(y, m, calendar.monthrange(y, m)[1]), "month"
    try:
        return date(y, m, d), "day"
    except ValueError:
        return None


def speak_date(d: date, granularity: str = "day") -> str:
    return f"{d.year}년 {d.month}월" + ("" if granularity == "month" else f" {d.day}일")


def has_josa_particle(text: str) -> str:
    """받침 유무에 따라 이/가 선택"""
    last = text[-1]
    code = ord(last) - 0xAC00
    return "이" if 0 <= code < 11172 and code % 28 else "가"


# ----------------------------------------------------------------------------
# 메인 클래스
# ----------------------------------------------------------------------------
class InfoExtractor:
    SOON_DAYS = 3                 # 소비기한 임박 기준 (일)
    LOW_CONF = 0.6                # OCR 신뢰도가 이보다 낮으면 '확인 필요'

    def __init__(self, allergen_path: str | Path = "allergens.json"):
        data = json.loads(Path(allergen_path).read_text(encoding="utf-8"))
        self.allergens = data["allergens"]
        self.guard: dict[str, list[str]] = {k: v for k, v in data["false_positive_guard"].items()
                                            if not k.startswith("_")}
        # (용어, 알레르겐, 직접표기 여부) - 긴 용어부터 매칭
        self.terms: list[tuple[str, dict, bool]] = []
        for a in self.allergens:
            self.terms += [(t, a, True) for t in a["aliases"]]
            self.terms += [(t, a, False) for t in a["derived"]]
            self.terms += [(t, a, False) for t in a.get("en", [])]
        self.terms.sort(key=lambda x: -len(x[0]))
        self.anchors = [(c, re.compile(p, re.I)) for c, p in ANCHORS]

    # ------------------------------------------------------------------ 입력
    @staticmethod
    def _to_lines(ocr: str | list) -> list[Line]:
        if isinstance(ocr, str):
            raw = [{"text": t} for t in ocr.splitlines() if t.strip()]
        else:
            raw = ocr
        lines = []
        for i, r in enumerate(raw):
            if isinstance(r, str):
                r = {"text": r}
            lines.append(Line(normalize(r["text"]), r.get("bbox"), float(r.get("conf", 1.0)), i))
        return lines

    # ------------------------------------------------------------- 1) 분절
    def segment(self, lines: list[Line]) -> list[Segment]:
        segs: list[Segment] = []
        prev_cat: str | None = None
        for ln in lines:
            # 문장 단위로 먼저 나눔 (숫자 사이 마침표는 날짜이므로 제외)
            sentences = [s.strip() for s in re.split(r"(?<!\d)\.(?!\d)|;", ln.text) if s.strip()]
            for sent in sentences:
                if CROSS_CONTACT_RE.search(sent):      # 혼입 문구는 문장 전체가 하나의 정보
                    segs.append(Segment("CROSS_CONTACT", sent, ln, "같은 제조시설"))
                    prev_cat = "CROSS_CONTACT"
                    continue
                hits = []
                for cat, rx in self.anchors:
                    for m in rx.finditer(sent):
                        if not any(m.start() < e and s < m.end() for s, e, _, _ in hits):
                            hits.append((m.start(), m.end(), cat, m.group(0)))
                hits.sort()
                if not hits:                           # 앵커 없음 -> 앞 카테고리를 이어받거나 미분류
                    cat = prev_cat if prev_cat in CONTINUABLE else None
                    if prev_cat in ("EXPIRY", "MFG_DATE") and segs and not self._find_dates(segs[-1].text, True):
                        cat = prev_cat                 # "소비기한" 다음 줄에 날짜만 있는 경우
                    segs.append(Segment(cat, sent, ln))
                    continue
                if hits[0][0] > 0:                     # 첫 앵커 앞부분
                    head = sent[:hits[0][0]].strip(" :")
                    if head:
                        segs.append(Segment(None, head, ln))
                for k, (s, e, cat, a) in enumerate(hits):
                    end = hits[k + 1][0] if k + 1 < len(hits) else len(sent)
                    body = sent[e:end].strip(" :-")
                    # 주의/보관 등은 앵커 단어 자체도 의미가 있으므로 문장 전체를 보존
                    text = sent[s:end].strip() if cat in ("CAUTION", "STORAGE") else body
                    segs.append(Segment(cat, text, ln, a))
                    prev_cat = cat
        return segs

    # ---------------------------------------------------------- 2) 날짜 해석
    def _find_dates(self, text: str, anchored: bool, conf: float = 1.0) -> list[DateHit]:
        found: list[tuple[int, int, DateHit]] = []
        for name, rx, needs_anchor in DATE_PATTERNS:
            if needs_anchor and not anchored:
                continue
            for m in rx.finditer(text):
                if any(m.start() < e and s < m.end() for s, e, _ in found):
                    continue
                g = m.groups()
                ambiguous = False
                if name in ("YMD_KOR", "YMD4", "YMD8", "YMD2", "YMD6"):
                    r = safe_date(int(g[0]), int(g[1]), int(g[2]))
                elif name == "DMY4":                    # 수입품: 13 이상이면 일(day)로 확정
                    a, b, y = int(g[0]), int(g[1]), int(g[2])
                    if a > 12:
                        r = safe_date(y, b, a)
                    elif b > 12:
                        r = safe_date(y, a, b)
                    else:
                        r, ambiguous = safe_date(y, b, a), a != b   # 기본 일/월/년(EU식)으로 보고 모호 표시
                elif name == "D_MON_Y":
                    r = safe_date(int(g[2]), MONTHS_EN[g[1].upper()[:3]], int(g[0]))
                elif name == "MON_D_Y":
                    r = safe_date(int(g[2]), MONTHS_EN[g[0].upper()[:3]], int(g[1]))
                else:                                   # YM_KOR / YM4
                    r = safe_date(int(g[0]), int(g[1]), None)
                if r is None:
                    continue
                d, gran = r
                if not (date(2000, 1, 1) <= d <= date(2045, 12, 31)):
                    continue
                found.append((m.start(), m.end(), DateHit(d, m.group(0), name, gran, ambiguous, conf)))
        return [h for _, _, h in sorted(found, key=lambda x: x[0])]

    def resolve_dates(self, segs: list[Segment], lines: list[Line]) -> dict[str, Any]:
        expiry: list[DateHit] = []
        mfg: list[DateHit] = []
        elsewhere = False
        relative: tuple[int, str] | None = None
        for sg in segs:
            if sg.category == "EXPIRY":
                expiry += self._find_dates(sg.text, True, sg.line.conf)
                if DATE_ELSEWHERE_RE.search(sg.text):
                    elsewhere = True
                rm = RELATIVE_RE.search(sg.text)
                if rm:
                    relative = (int(rm.group(1)), rm.group(2))
            elif sg.category == "MFG_DATE":
                mfg += self._find_dates(sg.text, True, sg.line.conf)

        notes = []
        # (a) "제조일로부터 12개월" -> 제조일자 기준 계산
        if not expiry and relative and mfg:
            n, unit = relative
            base = mfg[0].value
            if unit in ("개월", "달"):
                m = base.month - 1 + n
                y, m = base.year + m // 12, m % 12 + 1
                d = date(y, m, min(base.day, calendar.monthrange(y, m)[1])) - timedelta(days=1)
            elif unit == "년":
                d = date(base.year + n, base.month, base.day) - timedelta(days=1)
            else:
                d = base + timedelta(days=n - 1)
            expiry.append(DateHit(d, f"제조일로부터 {n}{unit}", "RELATIVE", conf=mfg[0].conf))
            notes.append("제조일자 기준으로 소비기한을 계산함")
        # (b) "윗면 표기일까지" 또는 앵커 없이 날짜만 찍힌 경우 -> 분류 안 된 줄에서 날짜 탐색
        if not expiry:
            mfg_vals = {h.value for h in mfg}
            loose = []
            for sg in segs:
                if sg.category in (None, "EXPIRY"):
                    loose += [h for h in self._find_dates(sg.text, elsewhere, sg.line.conf)
                              if h.value not in mfg_vals]
            if loose:
                best = max(loose, key=lambda h: h.value)  # 제조일보다 늦은 날짜가 소비기한일 가능성이 큼
                best.conf *= 0.8
                expiry.append(best)
                notes.append("소비기한 키워드 없이 찍힌 날짜를 소비기한으로 추정함")
        return {"expiry": expiry, "mfg": mfg, "date_elsewhere": elsewhere, "notes": notes}

    # ------------------------------------------------------- 3) 알레르겐 매칭
    def _tokens(self, text: str) -> list[str]:
        toks = re.split(r"[,()\[\]{}·/:%]+|\s+(?=[가-힣])|(?<=[가-힣])\s+|및|그리고|와|과|\band\b", text)
        out = []
        for t in toks:
            t = re.sub(r"(함유|포함|성분|등|을|를|이|가|사용한|사용|제품|일부)$", "", t.strip())
            t = re.sub(r"\d+(\.\d+)?", "", t)           # 함량 숫자 제거 (밀가루50 -> 밀가루)
            if t:
                out.append(t)
        return out

    def _guarded(self, term: str, token: str) -> bool:
        return any(g in token for g in self.guard.get(term, []))

    def match_allergens(self, text: str, status: str, conf: float = 1.0,
                        allow_fuzzy: bool = True) -> list[AllergenHit]:
        hits: dict[str, AllergenHit] = {}
        for tok in self._tokens(text.lower()):
            tok_hit = False
            for term, a, _direct in self.terms:
                if len(term) == 1:                       # 한 글자 용어(밀, 게, 콩, 굴, 잣)는 토큰 단위 완전 일치만
                    ok = tok == term
                elif term.isascii():                     # 영문은 단어 경계 기준
                    ok = re.search(rf"\b{re.escape(term)}\b", tok) is not None
                else:
                    ok = term in tok
                if ok and not self._guarded(term, tok):
                    tok_hit = True                       # 이미 찾은 알레르겐이어도 '정확히 일치한 토큰'이면 퍼지 검사 생략
                    if a["id"] not in hits:
                        hits[a["id"]] = AllergenHit(a["id"], a["label"], status, term,
                                                    high_risk=a["high_risk"], conf=conf)
            if tok_hit or not allow_fuzzy:
                continue
            # 퍼지 매칭: OCR 오탈자 대응 (자모 단위 편집거리)
            jt = to_jamo(tok)
            for term, a, _ in self.terms:
                if a["id"] in hits or len(term) < 2 or term.isascii():
                    continue
                limit = 1 if len(term) == 2 else 2
                jterm = to_jamo(term)
                windows = [tok] if len(tok) == len(term) else (
                    [tok[i:i + len(term)] for i in range(len(tok) - len(term) + 1)] if len(term) >= 3 else [])
                for w in windows:
                    if w[0] != term[0] and len(term) == 2:  # 두 글자는 첫 음절이 같아야 함
                        continue
                    if 0 < edit_distance(to_jamo(w), jterm) <= limit:
                        hits[a["id"]] = AllergenHit(a["id"], a["label"], status, f"{w}≈{term}",
                                                    fuzzy=True, high_risk=a["high_risk"], conf=conf * 0.7)
                        break
            _ = jt
        return list(hits.values())

    def resolve_allergens(self, segs: list[Segment]) -> list[AllergenHit]:
        result: dict[str, AllergenHit] = {}
        rank = {"CONTAINS": 3, "INFERRED": 2, "MAY_CONTAIN": 1}

        def add(hs: list[AllergenHit]):
            for h in hs:
                cur = result.get(h.id)
                # 직접 표기 > 원재료 추론 > 혼입 가능, 같으면 퍼지 아닌 쪽 우선
                if cur is None or rank[h.status] > rank[cur.status] or (
                        rank[h.status] == rank[cur.status] and cur.fuzzy and not h.fuzzy):
                    result[h.id] = h

        for sg in segs:
            if sg.category == "ALLERGEN" or (sg.category != "CROSS_CONTACT" and CONTAINS_RE.search(sg.text)
                                             and sg.category not in ("NUTRITION",)):
                add(self.match_allergens(sg.text, "CONTAINS", sg.line.conf))
            elif sg.category == "CROSS_CONTACT":
                add(self.match_allergens(sg.text, "MAY_CONTAIN", sg.line.conf))
            elif sg.category == "INGREDIENTS":
                add(self.match_allergens(sg.text, "INFERRED", sg.line.conf))
        return sorted(result.values(), key=lambda h: (-rank[h.status], h.label))

    # ------------------------------------------------------- 4) 기타 정보
    def resolve_product_name(self, segs: list[Segment], lines: list[Line]) -> str | None:
        for sg in segs:
            if sg.category == "PRODUCT_NAME" and sg.text:
                return sg.text
        anchored = {sg.line.idx for sg in segs if sg.category}
        cands = [ln for ln in lines if ln.idx not in anchored and re.search(r"[가-힣A-Za-z]{2,}", ln.text)
                 and sum(c.isdigit() for c in ln.text) / max(len(ln.text), 1) < 0.5]
        if not cands:
            return None
        if any(ln.bbox for ln in cands):                # 글자가 가장 큰 줄 = 제품명일 가능성이 높음
            return max(cands, key=lambda ln: ln.height).text
        return cands[0].text

    # ------------------------------------------------------- 5) 위험 판단
    def judge(self, expiry: list[DateHit], allergens: list[AllergenHit],
              user_allergies: list[str], today: date) -> dict[str, Any]:
        out: dict[str, Any] = {"expiry_status": "UNKNOWN", "days_left": None,
                               "user_allergen_hits": [], "needs_recheck": [], "level": "INFO"}
        if expiry:
            e = expiry[0]
            days = (e.value - today).days
            out["days_left"] = days
            out["expiry_status"] = ("EXPIRED" if days < 0 else "TODAY" if days == 0
                                    else "SOON" if days <= self.SOON_DAYS else "OK")
            if e.ambiguous or e.conf < self.LOW_CONF:
                out["needs_recheck"].append("EXPIRY")
        else:
            out["needs_recheck"].append("EXPIRY")

        profile = set()
        for u in user_allergies:
            for a in self.allergens:
                if u.lower() in (a["id"].lower(), a["label"]) or u in a["aliases"] or u.lower() in a.get("en", []):
                    profile.add(a["id"])
        for h in allergens:
            if h.id in profile:
                out["user_allergen_hits"].append(h.label)
            if h.fuzzy or h.conf < self.LOW_CONF:
                out["needs_recheck"].append(f"ALLERGEN:{h.label}")

        if out["user_allergen_hits"] or out["expiry_status"] == "EXPIRED":
            out["level"] = "DANGER"
        elif out["expiry_status"] in ("TODAY", "SOON") or out["needs_recheck"]:
            out["level"] = "WARNING"
        return out

    # ------------------------------------------------------- 6) 음성 문장
    def build_tts(self, info: dict, risk: dict) -> list[str]:
        s: list[str] = []
        exp: DateHit | None = info["expiry"][0] if info["expiry"] else None
        # (1) 사용자 알레르기 일치
        for label in risk["user_allergen_hits"]:
            h = next(x for x in info["allergens"] if x.label == label)
            if h.fuzzy:
                s.append(f"주의. 회원님이 피해야 할 {label}{has_josa_particle(label)} 들어 있을 수 있습니다. "
                         f"글자가 흐려 확인이 필요합니다.")
            elif h.status == "MAY_CONTAIN":
                s.append(f"주의. 회원님이 피해야 할 {label}{has_josa_particle(label)} 섞여 있을 수 있습니다.")
            else:
                s.append(f"주의. 회원님이 피해야 할 {label}{has_josa_particle(label)} 들어 있습니다.")
        # (2) 소비기한 경과
        if risk["expiry_status"] == "EXPIRED":
            s.append(f"주의. 소비기한이 {-risk['days_left']}일 지났습니다.")
        # (3) 제품명
        if info["product_name"]:
            s.append(f"제품명은 {info['product_name']}입니다.")
        # (4) 소비기한
        if exp and "EXPIRY" not in risk["needs_recheck"]:
            tail = {"TODAY": "오늘까지입니다.",
                    "SOON": "내일까지입니다." if risk["days_left"] == 1 else f"{risk['days_left']}일 남았습니다.",
                    "OK": f"{risk['days_left']}일 남았습니다.", "EXPIRED": "입니다."}[risk["expiry_status"]]
            s.append(f"소비기한은 {speak_date(exp.value, exp.granularity)}, {tail}".replace(", 입니다", "입니다"))
        elif exp:
            s.append(f"소비기한은 {speak_date(exp.value, exp.granularity)}로 보이지만 확실하지 않습니다. "
                     f"날짜 부분을 가까이에서 다시 촬영해 주세요.")
        else:
            s.append("소비기한을 찾지 못했습니다. 날짜가 적힌 면을 다시 촬영해 주세요.")
        # (5) 알레르기 성분
        contains = [h for h in info["allergens"] if h.status in ("CONTAINS", "INFERRED") and not h.fuzzy]
        if contains:
            s.append(f"알레르기 성분: {', '.join(h.label for h in contains)}.")
        fuzzy = [h for h in info["allergens"] if h.fuzzy and h.label not in risk["user_allergen_hits"]]
        if fuzzy:
            s.append(f"{', '.join(h.label for h in fuzzy)} 성분이 있을 수 있으나 글자가 흐려 확인이 필요합니다.")
        # (6) 혼입 가능
        may = [h for h in info["allergens"] if h.status == "MAY_CONTAIN" and not h.fuzzy]
        if may:
            s.append(f"같은 시설에서 {', '.join(h.label for h in may)}{'을' if has_josa_particle(may[-1].label) == '이' else '를'} "
                     f"사용한 제품을 만듭니다.")
        # (7) 주의사항 요약 (최대 2개)
        for c in info["cautions"][:2]:
            s.append(c if c.endswith(".") else c + ".")
        return s

    # ------------------------------------------------------------- 전체
    def extract(self, ocr: str | list, user_allergies: list[str] | None = None,
                today: str | date | None = None) -> dict[str, Any]:
        today = date.fromisoformat(today) if isinstance(today, str) else (today or date.today())
        lines = self._to_lines(ocr)
        segs = self.segment(lines)
        dates = self.resolve_dates(segs, lines)
        allergens = self.resolve_allergens(segs)
        info = {
            "product_name": self.resolve_product_name(segs, lines),
            "expiry": dates["expiry"],
            "mfg": dates["mfg"],
            "allergens": allergens,
            "cautions": [sg.text for sg in segs if sg.category == "CAUTION"],
        }
        risk = self.judge(info["expiry"], allergens, user_allergies or [], today)
        tts = self.build_tts(info, risk)

        def dh(h: DateHit) -> dict:
            return {"date": h.value.isoformat(), "raw": h.raw, "format": h.fmt,
                    "granularity": h.granularity, "ambiguous": h.ambiguous, "conf": round(h.conf, 2)}

        return {
            "product_name": info["product_name"],
            "expiry": dh(info["expiry"][0]) if info["expiry"] else None,
            "mfg_date": dh(info["mfg"][0]) if info["mfg"] else None,
            "allergens": [vars(h) | {"conf": round(h.conf, 2)} for h in allergens],
            "cautions": info["cautions"],
            "categories": {c: [sg.text for sg in segs if sg.category == c] for c in CATEGORIES
                           if any(sg.category == c for sg in segs)},
            "risk": risk,
            "notes": dates["notes"],
            "tts_script": " ".join(tts),
        }


if __name__ == "__main__":
    import sys
    ex = InfoExtractor(Path(__file__).with_name("allergens.json"))
    text = sys.stdin.read() if not sys.stdin.isatty() else "제품명: 테스트 우유\n소비기한 2026.11.10까지\n알레르기: 우유 함유"
    print(json.dumps(ex.extract(text, user_allergies=["우유"]), ensure_ascii=False, indent=2))
