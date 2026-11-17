"""OCR 줄에서 제품명, 소비기한, 알레르기, 원재료, 영양정보, 주의사항 뽑기.  담당: 이서영

규칙
- 제품명: '제품명:' 칸 → 표의 '제품명' 칸 오른쪽 글자(박스 좌표) → 제품명 영역 박스 → 가장 큰 글씨(박스 높이 × 신뢰도)
- 알레르기 함유
  - '○○ 함유' 줄. '함유'만 따로 읽힌 줄이면 바로 위 성분 목록 줄을 본다 (실제 사진: '밀,새우,…,쇠고기' / '함유')
  - '알레르기 유발물질: ○○' 줄 (콜론 뒤가 비면 다음 줄)
  - 원재료명 속 성분 (밀가루(밀), 탈지분유(우유), 소맥분, 대두유 …) → 함유로 추가
- 혼입 가능: '○○를 사용한 제품과 같은 (제조)시설', '혼입될 수 있습니다', '같은 시설에서 ○○를 사용한 제품을 제조',
  'may contain ○○'. 줄이 나뉘거나 '이 제품은' 없이 '·'로 시작해도 처리
- 원재료: '원재료명:' 줄 (쉼표로 끝나면 다음 줄까지). 표처럼 '원재료명' 칸이 따로 있으면 오른쪽 칸의 여러 줄
- 알레르기 표시란·혼입 문구에서만 OCR 오탈자를 맞춘다 (유유→우유, 토미토→토마토, 홍한→홍합(조개류))

날짜와 '제품명:' 칸은 박스 단위로, 나머지는 같은 줄 박스를 이어 붙인 줄 단위로 본다.
"""

from __future__ import annotations

import datetime as dt
import re

from ._deps import Config, kst_today
from ._deps import box_height, clean_ocr_text, group_lines, poly_bbox
from ._deps import Allergens, Fields, TextBox, TextField, UserProfile
from .allergens import find_allergens_ocr, ingredient_allergens
from .dates import choose_expiry
from .risk import build_expiry, match_profile

_PRODUCT_KEY = re.compile(r"제\s*[품풍픔l1I|]\s*명\s*[:：]?\s*(.*)")
_NAME_STOP = re.compile(
    r"소비|유통|기한|원재료|알레르기|유발|함유|보관|영양|제조|kcal|나트륨|식품\s*유형|내용량|주의|섭취|반품|교환|고객|상담|www|http|\d{4}"
)
_HANGUL2 = re.compile(r"[가-힣].*[가-힣]")
# 표 형태 포장(제품명 | 매운새우깡)에서 '칸 이름'으로 쓰이는 말
_LABEL = re.compile(
    r"^\s*(?:제\s*품\s*명|식\s*품\s*유\s*형|원\s*재\s*료\s*명?(?:\s*및\s*함량)?|소\s*비\s*기\s*한|유\s*통\s*기\s*한|"
    r"품\s*질\s*유\s*지\s*기\s*한|품\s*목(?:\s*보\s*고\s*번\s*호)?|보\s*고\s*번\s*호|포\s*장\s*재\s*질|내\s*용\s*량|"
    r"보\s*관\s*방\s*법|영\s*양\s*정\s*보|제\s*조\s*원|판\s*매\s*원|제\s*조\s*사|수\s*입\s*원|원\s*산\s*지)\s*[:：]?\s*$"
)
_INGREDIENTS = re.compile(r"(?:원\s*재\s*료\s*명?\s*(?:및\s*함량)?|\bingredients?\b)\s*[:：]?\s*(.*)", re.I)
_NUTRITION = re.compile(r"kcal|나트륨|탄수화물|단백질|지방|영양\s*정보|영양\s*성분")
_CAUTION = re.compile(r"보관|주의|섭취\s*(?:시|후|전)|개봉\s*후")
_CONTAINS_WORDS = re.compile(r"알레\S?기|알러지|유발\s*물질")  # OCR 오타 '알레로기' 포함
_ANCHOR = re.compile(r"원재료|소비|유통|기한|보관|영양|제품명|식품\s*유형|내용량|품목|포장|제조원|판매원|kcal|ingredient", re.I)

# 혼입 가능 문구. items 가 성분 목록 쪽.
_FACILITY_BEFORE = re.compile(
    r"(?:사\s*용|취\s*급)\s*(?:한|하는|하고\s*있는)?\s*제\s*품\s*(?:과|와)\s*(?:같은|동일한?)\s*(?:제\s*조\s*)?(?:시\s*설|공\s*장|라\s*인|설\s*비)"
    r"|혼\s*입\s*(?:될|되었을|되어\s*있을|가능)"
    r"|(?:함유|포함|들어\s*갈)\s*될?\s*수\s*있습니다"
)
_FACILITY_AFTER = re.compile(
    r"(?:같은|동일한?)\s*(?:제\s*조\s*)?(?:시\s*설|공\s*장|라\s*인|설\s*비)에서\s*(?P<items>[^.]{1,80}?)\s*(?:을|를)?\s*(?:사\s*용|취\s*급)\s*(?:한|하는)\s*제\s*품"
)
_MAY_CONTAIN_EN = re.compile(r"may\s+contain\s*(?:traces\s+of\s*)?(?P<items>[a-z ,&]+)", re.I)
_FACILITY_WORDS = re.compile(r"제\s*조\s*시\s*설|같은\s*시\s*설|동일\s*시설|혼\s*입|may\s+contain", re.I)
# 혼입 문장이 시작되는 곳: 문장 끝, 글머리표, 콜론, '함유'
_SENT_BOUNDARY = re.compile(r"[.。·•※:：]|함\s*유|니다|\n\s*[-*]")
_THIS_PRODUCT = re.compile(r"이\s*제\s*품\s*은")


Line = tuple[str, float, str | None]


def _box_lines(boxes: list[TextBox]) -> list[Line]:
    """박스 하나 = 한 줄 (날짜, '제품명:' 칸처럼 박스 단위가 정확한 것에 쓴다)."""
    return [(b.text.strip(), b.conf, b.region) for b in boxes if b.text.strip()]


def _visual_lines(boxes: list[TextBox]) -> list[Line]:
    """같은 줄에 있는 박스를 이어 붙인 한 줄 (문장이 박스 여러 개로 쪼개진 경우를 위해).

    신뢰도는 가장 낮은 박스 값을 쓴다 ('틀릴 수 있어요' 안내를 보수적으로 하려고).
    """
    out = []
    for group in group_lines([b for b in boxes if b.text.strip()]):
        regions = [b.region for b in group if b.region]
        out.append((
            " ".join(b.text.strip() for b in group),
            min(b.conf for b in group),
            max(set(regions), key=regions.count) if regions else None,
        ))
    return out


# ------------------------------------------------------------------ 표 형태 (칸 이름 | 내용)
def _bb(b: TextBox):
    return poly_bbox(b.poly) if b.poly else None


def _label_boxes(boxes: list[TextBox]) -> list[TextBox]:
    return [b for b in boxes if b.poly and _LABEL.match(b.text)]


def _same_row_right(label: TextBox, boxes: list[TextBox]) -> list[TextBox]:
    """칸 이름 박스와 같은 줄, 오른쪽에 있는 박스들 (다음 칸 이름 앞까지)."""
    lx1, ly1, lx2, ly2 = _bb(label)
    h = max(1.0, ly2 - ly1)
    row = []
    for b in boxes:
        if b is label or not b.poly:
            continue
        x1, y1, x2, y2 = _bb(b)
        overlap = min(ly2, y2) - max(ly1, y1)
        if x1 >= lx2 - 0.5 * h and overlap >= 0.5 * min(h, y2 - y1):
            row.append(b)
    row.sort(key=lambda b: _bb(b)[0])
    out = []
    for b in row:
        if _LABEL.match(b.text) or _LABEL.match(b.text.split(" ")[0] if b.text.strip() else ""):
            break
        if out and _bb(b)[0] - _bb(out[-1])[2] > 4 * h:  # 멀리 떨어진 다음 칸
            break
        out.append(b)
    return out


def _label_block(label: TextBox, boxes: list[TextBox]) -> list[TextBox]:
    """칸 이름 오른쪽의 여러 줄 내용 (원재료명처럼 칸 이름이 내용의 세로 가운데에 있는 표).

    위아래 경계는 같은 열에 있는 앞 칸 이름의 줄 아래, 다음 칸 이름의 위.
    """
    lx1, ly1, lx2, ly2 = _bb(label)
    h = max(1.0, ly2 - ly1)
    column = [b for b in _label_boxes(boxes) if b is not label and abs(_bb(b)[0] - lx1) <= 3 * h]
    above = [b for b in column if _bb(b)[3] <= ly1 + 0.2 * h]
    below = [b for b in column if _bb(b)[1] >= ly2 - 0.2 * h]
    top = max((_bb(b)[3] for b in above), default=ly1 - 4 * h)
    bottom = min((_bb(b)[1] for b in below), default=ly2 + 4 * h)
    out = []
    for b in boxes:
        if b is label or not b.poly:
            continue
        x1, y1, x2, y2 = _bb(b)
        cy = (y1 + y2) / 2
        if x1 >= lx2 - 0.5 * h and top <= cy <= bottom and not _LABEL.match(b.text):
            out.append(b)
    return [b for g in group_lines(out) for b in g]


# ------------------------------------------------------------------ 제품명
def _clean_name(text: str) -> str:
    text = text.strip(" \"'`‘’“”:：·,.|/[]")
    text = re.split(r"\s*[|/]\s*|\s{2,}|식\s*[품문]\s*유\s*[형히]", text)[0]
    return text.strip(" \"'`‘’“”:：·,.|()[]")


def pick_product_name(boxes: list[TextBox], lines: list[Line]) -> TextField:
    # 1) '제품명: ○○' 이 한 박스에
    for text, conf, _ in lines:
        m = _PRODUCT_KEY.search(text)
        if m and m.group(1).strip():
            value = _clean_name(m.group(1))
            if len(value) >= 2:
                return TextField(value=value, conf=conf)
    # 2) 표의 '제품명' 칸 오른쪽 (실제 사진: '제품명' | '매운새우깡')
    for label in _label_boxes(boxes):
        if _PRODUCT_KEY.match(label.text.strip()):
            row = _same_row_right(label, boxes)
            if row:
                value = _clean_name(" ".join(b.text.strip() for b in row))
                if value:
                    return TextField(value=value, conf=min(b.conf for b in row))
    # 3) 영역 검출기가 제품명이라고 한 박스
    tagged = [b for b in boxes if b.region == "product_name" and b.text.strip()]
    if tagged:
        best = max(tagged, key=lambda b: b.conf)
        return TextField(value=_clean_name(best.text) or best.text.strip(), conf=best.conf)
    # 4) 가장 큰 글씨
    cands = [
        b for b in boxes
        if _HANGUL2.search(b.text) and not _NAME_STOP.search(b.text) and len(b.text.strip()) <= 30
        and not _LABEL.match(b.text)
    ]
    if not cands:
        return TextField()
    best = max(cands, key=lambda b: box_height(b) * b.conf)
    return TextField(value=_clean_name(best.text) or best.text.strip(), conf=best.conf)


# ------------------------------------------------------------------ 알레르기
def _after_label(segment: str) -> str:
    return re.split(r"[:：]", segment)[-1]


def _may_contain_spans(lines: list[Line]) -> list[tuple[str, str, int, int]]:
    """혼입 문구를 찾는다. [(성분 목록 글자, 근거 문장, 시작 줄, 끝 줄)]"""
    joined = "\n".join(t for t, _, _ in lines)
    starts = []
    pos = 0
    for t, _, _ in lines:
        starts.append(pos)
        pos += len(t) + 1

    def line_of(p: int) -> int:
        i = 0
        while i + 1 < len(starts) and starts[i + 1] <= p:
            i += 1
        return i

    out = []
    for m in _FACILITY_BEFORE.finditer(joined):
        lo = max(0, m.start() - 160)
        window = joined[lo: m.start()]
        cut = 0
        for b in _SENT_BOUNDARY.finditer(window):
            cut = b.end()
        sent_start = lo + cut
        # '소비기한 …', '포장재질 …', '알레르기 …' 같은 다른 칸 줄이 사이에 있으면 그 뒤부터 (성분 목록이 아님)
        offset = sent_start
        for ln in joined[sent_start: m.start()].split("\n")[:-1]:
            offset += len(ln) + 1
            if _ANCHOR.search(ln) or _CONTAINS_WORDS.search(ln) or re.search(r"함\s*유", ln):
                sent_start = offset
        while sent_start < m.start() and joined[sent_start] in " \n":
            sent_start += 1
        start = sent_start
        tp = _THIS_PRODUCT.search(joined, start, m.start())
        if tp:
            start = tp.end()
        items = joined[start: m.start()]
        sentence = joined[sent_start: m.end()].strip(" \n·•※-*")
        out.append((items, " ".join(sentence.split()), line_of(start), line_of(m.end())))
    for m in _FACILITY_AFTER.finditer(joined):
        out.append((m.group("items"), " ".join(m.group(0).split()), line_of(m.start()), line_of(m.end())))
    for m in _MAY_CONTAIN_EN.finditer(joined):
        out.append((m.group("items"), " ".join(m.group(0).split()), line_of(m.start()), line_of(m.end())))
    return out


def _is_allergen_list(text: str) -> bool:
    """성분 이름만 나열된 줄인지: 이름 4개 이상, 토큰의 60% 이상이 성분 이름."""
    if _ANCHOR.search(text) or _FACILITY_WORDS.search(text):
        return False
    toks = [t for t in re.split(r"[\s,·/;()]+", text) if t]
    found = find_allergens_ocr(text)
    return len(found) >= 4 and len(found) >= 0.6 * len(toks)


def parse_allergens(lines: list[Line], ingredients: str | None = None) -> tuple[Allergens, set[int]]:
    """(알레르기 정보, 근거로 쓴 줄 번호). 원재료 글자를 주면 그 속 성분도 함유에 더한다."""
    contains: list[str] = []
    may: list[str] = []
    raw: list[str] = []
    confs: list[float] = []
    used: set[int] = set()
    fuzzy_any = False

    def add(target: list[str], found: list[tuple[str, bool]]) -> None:
        nonlocal fuzzy_any
        for name, fz in found:
            if name not in target:
                target.append(name)
            fuzzy_any = fuzzy_any or fz

    # 1) 혼입 가능 문구 (줄바꿈이 있어도 되게 이어 붙여서 찾는다)
    facility_lines: set[int] = set()
    for items, sentence, a, b in _may_contain_spans(lines):
        found = find_allergens_ocr(items)
        if not found:
            continue
        add(may, found)
        raw.append(sentence)
        confs.append(min(lines[i][1] for i in range(a, b + 1)))
        facility_lines.update(range(a, b + 1))
    used |= facility_lines

    # 2) 함유 표시 줄
    for i, (text, conf, region) in enumerate(lines):
        if i in facility_lines or _FACILITY_WORDS.search(text):
            # 한 줄에 '○○ 함유. 이 제품은 …시설' 이 같이 있으면 함유 부분만 본다
            m = _FACILITY_BEFORE.search(text) or _FACILITY_WORDS.search(text)
            head = text[: m.start()] if m else ""
            if "함유" not in head:
                continue
            text = head
        segment, evidence = None, [i]
        if "함유" in text:
            segment = _after_label(text[: text.rfind("함유")])
            if not find_allergens_ocr(segment) and find_allergens_ocr(text[text.rfind("함유") + 2:]):
                # 기울어진 사진에서 '함유' 박스가 목록 앞에 붙은 경우 ('함유 밀,새우,…')
                segment = text[text.rfind("함유") + 2:]
            if not find_allergens_ocr(segment) and len(segment.strip(" ,.;:·()[]")) <= 1:
                # '함유'만 따로 읽힌 줄 → 바로 위 성분 목록 줄
                j = i - 1
                while j >= 0 and j not in facility_lines and len(evidence) <= 2:
                    prev = lines[j][0]
                    if _ANCHOR.search(prev) and not _CONTAINS_WORDS.search(prev):
                        break
                    if find_allergens_ocr(_after_label(prev)):
                        segment = _after_label(prev) + "," + (segment or "")
                        evidence.insert(0, j)
                        if len(find_allergens_ocr(_after_label(prev))) >= 2 or _CONTAINS_WORDS.search(prev):
                            break
                    j -= 1
        elif _CONTAINS_WORDS.search(text):
            after = _after_label(text) if re.search(r"[:：]", text) else _CONTAINS_WORDS.split(text)[-1]
            segment = after
            if not find_allergens_ocr(after) and i + 1 < len(lines) and i + 1 not in facility_lines:
                segment = lines[i + 1][0]  # 콜론 뒤가 비어 있으면 다음 줄
                evidence.append(i + 1)
        elif region == "allergen":
            segment = text
        elif _is_allergen_list(text):
            # '함유'를 못 읽었지만 성분 이름만 나열된 줄 (실제 사진: '밀새우 대투 유유 토마토 게 돼지고')
            segment = text
        if segment is None:
            continue
        found = find_allergens_ocr(segment)
        if found:
            add(contains, found)
            raw.append(" ".join(lines[j][0] for j in evidence))
            confs.append(min(lines[j][1] for j in evidence))
            used.update(evidence)

    # 3) 원재료명 속 성분 (표시를 OCR 이 놓쳐도 원재료에서 잡는다)
    if ingredients:
        inferred = [a for a in ingredient_allergens(ingredients) if a not in contains]
        if inferred:
            contains += inferred
            raw.append(f"원재료명에서 찾음: {', '.join(inferred)}")

    may = [a for a in may if a not in contains]  # 함유가 우선
    conf = min(confs) if confs else (0.0 if not contains else 0.5)
    if fuzzy_any:
        conf *= 0.8  # 오탈자로 맞춘 성분이 있으면 '틀릴 수 있어요' 안내가 나가게
    return Allergens(contains=contains, may_contain=may, raw=raw, conf=round(min(1.0, conf), 4)), used


# ------------------------------------------------------------------ 원재료·영양·주의
def parse_ingredients(boxes: list[TextBox], lines: list[Line]) -> tuple[TextField, set[int]]:
    """원재료명. (값, 쓴 줄 번호)

    '원재료명' 칸 이름만 따로 있는 표(박스 좌표가 있을 때)는 오른쪽 칸의 여러 줄을 먼저 본다.
    """
    for label in _label_boxes(boxes):
        if _INGREDIENTS.match(label.text.strip()):
            block = _label_block(label, boxes)
            block = [b for b in block if not re.fullmatch(r"\s*함\s*유\s*", b.text) and not _is_allergen_list(b.text)]
            if block:
                return TextField(value=" ".join(b.text.strip() for b in block), conf=min(b.conf for b in block)), set()
    for i, (text, conf, _) in enumerate(lines):
        m = _INGREDIENTS.search(text)
        if not m:
            continue
        value, confs, used = m.group(1).strip(" :："), [conf], {i}
        # 줄이 쉼표로 끝나면 다음 줄로 이어진 것으로 본다 (원재료 목록이 두 줄에 걸친 경우)
        j = i + 1
        while value.endswith(",") and j < len(lines) and not _ANCHOR.search(lines[j][0]):
            value += " " + lines[j][0]
            confs.append(lines[j][1])
            used.add(j)
            j += 1
        if value:
            return TextField(value=value, conf=min(confs)), used
        break
    return TextField(), set()


def _join_matching(lines: list[Line], rx, exclude=None) -> TextField:
    picked = [(t, c) for t, c, _ in lines if rx.search(t) and not (exclude and exclude.search(t))]
    if not picked:
        return TextField()
    return TextField(value=" ".join(t for t, _ in picked), conf=min(c for _, c in picked))


def _clean_box(b: TextBox) -> TextBox:
    t = clean_ocr_text(b.text)
    return b if t == b.text else b.model_copy(update={"text": t})


def extract_fields(
    boxes: list[TextBox],
    profile: UserProfile | None = None,
    today: dt.date | None = None,
    cfg: Config | None = None,
) -> Fields:
    cfg = cfg or Config()
    today = today or kst_today()
    boxes = [_clean_box(b) for b in boxes]  # 파이프라인을 거치지 않고 바로 불러도 O→0 등을 고친다
    box_lines = _box_lines(boxes)
    lines = _visual_lines(boxes)
    pairs = [(t, c) for t, c, _ in box_lines]

    choice = choose_expiry(pairs, today)
    expiry = build_expiry(choice.cand, choice.elsewhere, today, cfg, choice.pending)
    ingredients, _ = parse_ingredients(boxes, lines)
    allergens, _ = parse_allergens(lines, ingredients.value)
    allergens = match_profile(allergens, profile)
    return Fields(
        product_name=pick_product_name(boxes, box_lines),
        expiry=expiry,
        allergens=allergens,
        ingredients=ingredients,
        nutrition=_join_matching(lines, _NUTRITION),
        caution=_join_matching(lines, _CAUTION, exclude=_FACILITY_WORDS),
    )
