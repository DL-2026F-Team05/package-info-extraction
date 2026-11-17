"""알레르기 유발물질 사전과 이름 맞추기.  담당: 이서영

표시 대상 19종: 「식품등의 표시기준」 알레르기 유발물질 표시 대상
(알류·우유·메밀·땅콩·대두·밀·고등어·게·새우·돼지고기·복숭아·토마토·아황산류·호두·닭고기·쇠고기·오징어·조개류(굴·전복·홍합 포함)·잣).

이름을 맞추는 방법은 세 가지이고, 쓰는 곳이 다르다.

| 함수 | 쓰는 곳 | 규칙 |
|---|---|---|
| canonical / find_allergens | 어디서나 | 표준 이름·다른 이름과 정확히 같을 때 (조사·마지막 한 글자 오인식은 떼어 봄) |
| find_allergens_ocr | 알레르기 표시란, 혼입 문구 | 위 + 파생 원재료(두유→대두) + OCR 오탈자(유유→우유, 토미토→토마토, 돼지고→돼지고기) |
| ingredient_allergens | 원재료명 | 원재료 이름 안에 든 성분(소맥분→밀, 탈지분유→우유, 대두유→대두) |

오탈자 맞추기는 '알레르기 성분을 나열한 줄'에서만 쓴다. 아무 문장에나 쓰면 '함유', '포함' 같은 말이 성분으로 잡힌다.
"""

from __future__ import annotations

import re
import unicodedata

ALLERGENS: tuple[str, ...] = (
    "알류", "우유", "메밀", "땅콩", "대두", "밀", "잣", "호두", "게", "새우",
    "오징어", "고등어", "조개류", "복숭아", "토마토", "닭고기", "돼지고기", "쇠고기", "아황산류",
)

# 아나필락시스(중증 알레르기 반응) 위험이 높아 특히 조심하는 성분
HIGH_RISK: frozenset[str] = frozenset({"알류", "우유", "메밀", "땅콩", "대두", "밀"})

# 표준 이름 → 포장의 '알레르기 표시'에 그대로 쓰이는 다른 이름
SYNONYMS: dict[str, tuple[str, ...]] = {name: (name,) for name in ALLERGENS}
SYNONYMS.update(
    {
        "알류": ("알류", "난류", "계란", "달걀", "가금류"),
        "쇠고기": ("쇠고기", "소고기", "우육"),
        "돼지고기": ("돼지고기", "돈육"),
        "닭고기": ("닭고기", "계육"),
        "조개류": ("조개류", "조개", "굴", "전복", "홍합"),
        "아황산류": ("아황산류", "아황산"),
    }
)

# 영어 표기 (수입품 표시, 사용자 등록용)
ENGLISH: dict[str, str] = {
    "egg": "알류", "eggs": "알류", "milk": "우유", "dairy": "우유", "buckwheat": "메밀",
    "peanut": "땅콩", "peanuts": "땅콩", "soy": "대두", "soya": "대두", "soybean": "대두", "soybeans": "대두",
    "wheat": "밀", "gluten": "밀", "pine nut": "잣", "pine nuts": "잣", "pinenut": "잣",
    "walnut": "호두", "walnuts": "호두", "crab": "게", "shrimp": "새우", "prawn": "새우",
    "squid": "오징어", "mackerel": "고등어", "shellfish": "조개류", "clam": "조개류", "oyster": "조개류",
    "abalone": "조개류", "mussel": "조개류", "peach": "복숭아", "tomato": "토마토", "tomatoes": "토마토",
    "chicken": "닭고기", "pork": "돼지고기", "beef": "쇠고기", "sulfite": "아황산류", "sulfites": "아황산류",
    "sulphite": "아황산류", "sulphites": "아황산류",
    # 원재료명에서만 의미 있는 영어 원재료 이름
    "butter": "우유", "cheese": "우유", "whey": "우유", "casein": "우유", "lactose": "우유",
}

# 원재료 이름 → 그 안에 든 알레르기 성분. 원재료명 칸에서는 '이 말이 들어 있으면' 잡는다(부분 일치).
# 대표적인 것만 넣었다. 식물성 크림, 호밀처럼 헷갈리는 것은 일부러 뺐다.
DERIVED: dict[str, tuple[str, ...]] = {
    "밀": ("밀가루", "소맥분", "소맥", "글루텐", "빵가루", "강력분", "중력분", "박력분", "통밀",
          "밀전분", "부침가루", "튀김가루", "밀싹"),  # 맥아는 보리라서 넣지 않는다
    "우유": ("원유", "탈지분유", "전지분유", "혼합분유", "분유", "유청", "유단백", "우유단백", "버터", "치즈",
           "생크림", "유크림", "연유", "카제인", "유당", "락토스", "발효유", "요구르트", "요거트", "유제품"),
    "대두": ("대두유", "두유", "두부", "간장", "된장", "춘장", "청국장", "대두단백", "콩단백", "대두레시틴",
           "콩기름", "콩가루", "유부"),
    "알류": ("전란", "난백", "난황", "마요네즈", "메추리알", "계란", "달걀"),
    "땅콩": ("땅콩버터", "피넛", "낙화생"),
    "메밀": ("메밀가루",),
    "새우": ("쉬림프", "새우젓", "건새우"),
    "게": ("꽃게", "대게", "홍게", "게살", "크랩"),
    "조개류": ("굴소스", "바지락", "가리비", "대합", "모시조개", "재첩", "꼬막", "홍합", "전복", "조개"),
    "돼지고기": ("돈육", "돼지", "돈지", "라드", "베이컨", "돈골"),
    "쇠고기": ("우육", "소고기", "우지", "사골", "비프", "소뼈"),
    "닭고기": ("계육", "닭", "치킨"),
    "토마토": ("케첩", "케찹"),
    "아황산류": ("이산화황", "메타중아황산", "무수아황산"),
}

_LOOKUP: dict[str, str] = {syn: canon for canon, syns in SYNONYMS.items() for syn in syns}
_DERIVED_LOOKUP: dict[str, str] = {d: canon for canon, ds in DERIVED.items() for d in ds}
_SPLIT = re.compile(r"[\s,·ㆍ/()\[\]{}:;|+、.]+")
# 이름 뒤에 붙는 말. 떼고 나서 사전에 있는 이름이 될 때만 뗀다 (함부로 자르지 않으려고)
_SUFFIXES = ("함유", "포함", "등", "을", "를", "와", "과", "이", "가", "은", "는", "도", "및", "의")
_HANGUL = re.compile(r"^[가-힣]+$")
_EN_WORD = re.compile(r"[a-z]+(?:\s+nuts?)?")


def _norm(token: str) -> str:
    return unicodedata.normalize("NFC", token).strip(" .\"'`‘’“”-_*~!?")


def canonical(token: str) -> str | None:
    """'계란' → '알류', '새우를' → '새우', '새우틀' → '새우', 'milk' → '우유', '밀가루' → None"""
    t = _norm(token)
    if not t:
        return None
    if t in _LOOKUP:
        return _LOOKUP[t]
    low = t.lower()
    if low in ENGLISH:
        return ENGLISH[low]
    for suf in _SUFFIXES:
        if t.endswith(suf) and t[: -len(suf)] in _LOOKUP:
            return _LOOKUP[t[: -len(suf)]]
    # OCR 이 조사를 잘못 읽은 경우 ('새우를'→'새우틀', '호두를'→'호두름'): 마지막 한 글자를 떼어 본다.
    # 한 글자 이름(밀, 게, 잣)은 '게시', '밀폐' 같은 오탐이 나서 제외
    if len(t) >= 3 and len(t[:-1]) >= 2 and t[:-1] in _LOOKUP:
        return _LOOKUP[t[:-1]]
    return None


def find_allergens(segment: str) -> list[str]:
    """쉼표·괄호 등으로 나뉜 목록에서 표준 이름을 순서대로, 중복 없이 뽑는다.

    한 글자 이름(밀, 게, 잣)은 토큰이 정확히 같을 때만 잡힌다. '밀폐', '하게' 같은 오탐을 막으려고.
    """
    out: list[str] = []
    for tok in _SPLIT.split(segment):
        name = canonical(tok)
        if name and name not in out:
            out.append(name)
    return out


def canonicalize_list(names) -> list[str]:
    """사용자 프로필처럼 이름 목록을 표준 이름으로 바꾼다. 모르는 이름은 버린다.

    '계란', 'milk', '밀가루', '두유'처럼 원재료 이름으로 등록해도 해당 성분으로 바꾼다.
    """
    out: list[str] = []
    for n in names or []:
        t = _norm(str(n))
        c = canonical(t) or _DERIVED_LOOKUP.get(t)
        if c and c not in out:
            out.append(c)
    return out


# ------------------------------------------------------------------ OCR 오탈자까지 견디는 맞추기
_CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_JONG = " ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"


def to_jamo(s: str) -> str:
    """'우유' → 'ㅇㅜㅇㅠ'. 한 글자 안의 획 하나가 틀린 오인식을 거리 1로 보려고 자모로 나눈다."""
    out = []
    for ch in s:
        code = ord(ch) - 0xAC00
        if 0 <= code < 11172:
            out.append(_CHO[code // 588])
            out.append(_JUNG[(code % 588) // 28])
            if code % 28:
                out.append(_JONG[code % 28])
        else:
            out.append(ch)
    return "".join(out)


def _edit(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


# 오탈자 비교 대상: 두 글자 이상인 한글 이름 (한 글자는 오탐이 많아서 뺀다)
_FUZZY_TARGETS: tuple[tuple[str, str], ...] = tuple(
    (syn, canon) for syn, canon in _LOOKUP.items() if len(syn) >= 2 and _HANGUL.match(syn)
)
# 성분 목록에 자주 섞이지만 성분이 아닌 말 (오탈자 맞추기에서 제외)
_NOT_ALLERGEN = frozenset({"함유", "포함", "제품", "사용", "시설", "제조", "원료", "성분", "일부", "혼입", "같은", "동일"})


def _strip_suffix(t: str) -> str:
    for suf in _SUFFIXES:
        if len(t) > len(suf) + 1 and t.endswith(suf):
            return t[: -len(suf)]
    return t


def fuzzy_canonical(token: str) -> str | None:
    """OCR 오탈자를 견디는 이름 맞추기. 알레르기 목록 줄에서만 쓴다.

    - 두 글자 이름: 자모 하나만 다르고 한 글자는 같을 때 ('유유' → 우유)
    - 세 글자 이상: 글자 수가 같고 자모 두 개 이하로 다를 때 ('토미토' → 토마토, '홍한' 은 두 글자 규칙)
    - 네 글자 이름의 앞 세 글자만 읽힌 경우 ('돼지고' → 돼지고기)
    가장 가까운 이름이 둘 이상이면 맞추지 않는다 (애매하면 말하지 않는다).
    """
    t = _strip_suffix(_norm(token))
    if len(t) < 2 or not _HANGUL.match(t) or t in _NOT_ALLERGEN or t in _DERIVED_LOOKUP:
        return None  # 두유·원유처럼 그 자체로 다른 성분의 원재료인 말은 오탈자로 보지 않는다
    jt = to_jamo(t)
    best: list[tuple[int, str]] = []
    for syn, canon in _FUZZY_TARGETS:
        d = None
        if len(syn) == 2 and len(t) == 2:
            if t[0] == syn[0] or t[1] == syn[1]:
                d = _edit(jt, to_jamo(syn))
                d = d if d <= 1 else None
        elif len(syn) >= 3 and len(t) == len(syn):
            d = _edit(jt, to_jamo(syn))
            d = d if d <= 2 and t[0] == syn[0] else None
        elif len(syn) >= 4 and len(t) == len(syn) - 1 and syn.startswith(t):
            d = 2
        if d is not None and d > 0:
            best.append((d, canon))
    if not best:
        return None
    best.sort()
    top = [c for d, c in best if d == best[0][0]]
    return top[0] if len(set(top)) == 1 else None


def _split_joined(t: str) -> list[str] | None:
    """'밀새우' 처럼 이름이 붙어 읽힌 토큰을 사전 이름들로 남김없이 나눌 수 있으면 나눈다."""
    t = _strip_suffix(t)
    if len(t) < 3 or not _HANGUL.match(t):
        return None

    def go(rest: str) -> list[str] | None:
        if not rest:
            return []
        for n in range(min(4, len(rest)), 0, -1):
            head = rest[:n]
            if head in _LOOKUP:
                tail = go(rest[n:])
                if tail is not None:
                    return [_LOOKUP[head]] + tail
        return None

    parts = go(t)
    return parts if parts and len(parts) >= 2 else None


def find_allergens_ocr(segment: str) -> list[tuple[str, bool]]:
    """알레르기 표시란·혼입 문구용. [(표준 이름, 오탈자로 맞췄는지)] 순서대로, 중복 없이.

    정확히 맞는 이름 → 파생 원재료 이름(두유, 계란) → 오탈자 순서로 본다.
    """
    out: list[tuple[str, bool]] = []
    seen: set[str] = set()
    low = unicodedata.normalize("NFC", segment).lower()
    for tok in _SPLIT.split(segment):
        t = _norm(tok)
        if not t:
            continue
        name, fuzzy = canonical(t), False
        if name is None:
            name = _DERIVED_LOOKUP.get(_strip_suffix(t)) or _DERIVED_LOOKUP.get(t)
        if name is None:
            parts = _split_joined(t)
            if parts:  # 쉼표를 못 읽어 붙은 이름 ('밀새우' → 밀, 새우)
                for p in parts:
                    if p not in seen:
                        seen.add(p)
                        out.append((p, False))
                continue
            name, fuzzy = fuzzy_canonical(t), True
        if name and name not in seen:
            seen.add(name)
            out.append((name, fuzzy and canonical(t) is None))
    # 'pine nuts' 처럼 두 단어인 영어 이름
    for m in _EN_WORD.finditer(low):
        name = ENGLISH.get(m.group(0))
        if name and name not in seen:
            seen.add(name)
            out.append((name, False))
    return out


def ingredient_allergens(text: str) -> list[str]:
    """원재료명 글자에서 알레르기 성분을 찾는다. '밀가루(밀:미국산)', '쉬림프크래커중국산소맥분' 모두 처리.

    - 괄호 안에 적힌 표준 이름 (밀가루(밀), 탈지분유(우유)) → 정확히 맞추기
    - 두 글자 이상 이름·원재료 이름이 붙어 쓴 글자 안에 들어 있으면 (소맥분, 대두유, 새우) → 부분 일치
    """
    out: list[str] = []

    def add(name: str | None) -> None:
        if name and name not in out:
            out.append(name)

    text = unicodedata.normalize("NFC", text)
    for name in find_allergens(text):
        add(name)
    compact = re.sub(r"\s+", "", text)
    hits: list[tuple[int, str]] = []
    for word, canon in list(_LOOKUP.items()) + list(_DERIVED_LOOKUP.items()):
        if len(word) < 2 and word != "닭":
            continue
        pos = compact.find(word)
        if pos >= 0:
            hits.append((pos, canon))
    for _, canon in sorted(hits):
        add(canon)
    low = text.lower()
    for m in re.finditer(r"[a-z]+(?:\s+nuts?)?", low):
        add(ENGLISH.get(m.group(0)))
    return out
