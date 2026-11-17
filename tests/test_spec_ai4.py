"""정보 추출 명세 테스트.

AI 4(박세준)가 AI_tts_web tests/test_extract_spec.py 에 정해 둔 명세를 그대로 가져왔다.
그쪽에서 xfail(아직 못 함)로 표시돼 있던 6개(두 자리 연도, OCR 오타, 구분자 없는 날짜, 상대 표기,
원재료 속 성분, 다른 혼입 표현)도 이 모듈은 통과하므로 xfail 표시를 뺐다.
"""

import datetime as dt

import pytest

from extraction import assess_risk, canonical, extract_fields, find_allergens
from extraction._deps import Config, TextBox, UserProfile

TODAY = dt.date(2026, 11, 10)


def run(*lines, profile=None, conf=0.95):
    boxes = [TextBox(text=t, conf=conf) for t in lines]
    return extract_fields(boxes, profile, TODAY, Config())


# ------------------------------------------------------------------ 날짜
@pytest.mark.parametrize(
    "line",
    ["소비기한 2026.11.20까지", "소비기한: 2026-11-20", "소비기한 2026/11/20", "소비기한 2026년 11월 20일까지", "소비기한: 2026 . 11 . 20"],
)
def test_date_formats(line):
    e = run(line).expiry
    assert e.date == dt.date(2026, 11, 20)
    assert e.kind == "use_by"
    assert e.status == "ok"
    assert e.days_left == 10


def test_keyword_on_previous_short_line():
    e = run("소비기한", "2026.11.20까지").expiry
    assert e.kind == "use_by" and e.date == dt.date(2026, 11, 20)


def test_manufacture_date_is_not_expiry():
    e = run("제조일자: 2026.05.20", "소비기한: 2026.11.20까지").expiry
    assert e.date == dt.date(2026, 11, 20)
    e = run("소비기한: 2026.11.20까지 / 제조일자: 2026.05.20").expiry
    assert e.date == dt.date(2026, 11, 20)


def test_sell_by_and_expired_and_soon():
    assert run("유통기한 2026.11.25까지").expiry.kind == "sell_by"
    assert run("소비기한 2026.11.01까지").expiry.status == "expired"
    assert run("소비기한 2026.11.12까지").expiry.status == "soon"


def test_elsewhere():
    e = run("소비기한: 별도표기일까지").expiry
    assert e.status == "elsewhere" and e.date is None


def test_low_confidence_abstains():
    e = run("소비기한 2026.11.20까지", conf=0.3).expiry
    assert e.status == "unknown"  # 판단 보류 (괜찮다고 말하지 않음)


def test_invalid_date_ignored():
    assert run("소비기한 2026.13.45까지").expiry.date is None


def test_two_digit_year():
    assert run("소비기한 26.11.20까지").expiry.date == dt.date(2026, 11, 20)


def test_ocr_typos():
    assert run("소비기한 2O26.1l.2O까지").expiry.date == dt.date(2026, 11, 20)


def test_compact_date():
    assert run("EXP 20261120").expiry.date == dt.date(2026, 11, 20)


def test_relative_expiry():
    e = run("제조일자: 2026.05.20", "소비기한: 제조일로부터 12개월").expiry
    assert e.date == dt.date(2027, 5, 19)


# ------------------------------------------------------------------ 알레르기
def test_canonical_names():
    assert canonical("계란") == "알류"
    assert canonical("소고기") == "쇠고기"
    assert canonical("새우를") == "새우"
    assert canonical("밀가루") is None
    assert canonical("밀폐") is None


def test_single_char_allergens_need_exact_token():
    assert find_allergens("밀, 게, 잣 함유") == ["밀", "게", "잣"]
    assert find_allergens("이렇게 밀폐 보관하게") == []


def test_contains_line():
    a = run("알레르기 유발물질: 밀, 대두, 우유 함유").allergens
    assert a.contains == ["밀", "대두", "우유"]
    assert run("대두 함유").allergens.contains == ["대두"]
    assert run("밀, 우유, 알류(계란) 함유").allergens.contains == ["밀", "우유", "알류"]


def test_facility_sentence_is_may_contain():
    a = run("이 제품은 땅콩, 새우를 사용한 제품과 같은 제조시설에서 제조하고 있습니다.").allergens
    assert a.may_contain == ["땅콩", "새우"]
    assert a.contains == []


def test_facility_sentence_split_over_two_lines():
    a = run("이 제품은 땅콩, 새우를 사용한 제품과", "같은 제조시설에서 제조하고 있습니다.").allergens
    assert a.may_contain == ["땅콩", "새우"]


def test_contains_beats_may():
    a = run("우유 함유", "이 제품은 우유, 땅콩을 사용한 제품과 같은 제조시설에서 제조").allergens
    assert a.contains == ["우유"] and a.may_contain == ["땅콩"]


def test_storage_line_is_not_allergen():
    assert run("보관방법: 밀폐하여 보관하세요").allergens.contains == []


def test_user_hits():
    a = run("밀, 우유 함유", "이 제품은 땅콩을 사용한 제품과 같은 제조시설에서 제조", profile=UserProfile(allergens=["우유", "땅콩", "메밀"])).allergens
    assert a.user_hits == ["우유"]
    assert a.user_may_hits == ["땅콩"]


def test_real_ocr_noise_in_allergen_lines():
    """EasyOCR 이 실제로 낸 실수: 를→틀/름, 알레르기→알레로기, 쉼표→세미콜론"""
    a = run("이 제품은 땅콩, 새우틀 사용한 제품과", "같은 제조시설에서 제조하고 있습니다.").allergens
    assert a.may_contain == ["땅콩", "새우"]
    a = run("이 제품은 땅콩, 호두름 사용한 제품과 같은 제조시설에서 제조").allergens
    assert a.may_contain == ["땅콩", "호두"]
    assert run("알레로기 유발물질: 밀; 대두, 우유").allergens.contains == ["밀", "대두", "우유"]


def test_canonical_tolerates_one_wrong_last_char():
    assert canonical("새우틀") == "새우"
    assert canonical("대두유") == "대두"  # 대두유(콩기름)는 대두 성분이 맞다
    assert canonical("게시") is None  # 한 글자 이름은 떼어 보지 않는다
    assert canonical("밀폐") is None


def test_allergens_from_ingredients():
    a = run("원재료명: 밀가루(밀:미국산), 설탕, 탈지분유(우유), 대두유").allergens
    assert set(a.contains) == {"밀", "우유", "대두"}


def test_other_may_contain_phrases():
    a = run("땅콩, 호두가 혼입될 수 있습니다.").allergens
    assert a.may_contain == ["땅콩", "호두"]


# ------------------------------------------------------------------ 제품명·위험도
def test_product_name_from_field():
    f = run("제품명: 바삭 고소칩 | 식품유형: 과자", "소비기한 2026.11.20까지")
    assert f.product_name.value == "바삭 고소칩"


def test_risk_levels():
    danger = run("우유 함유", profile=UserProfile(allergens=["우유"]))
    assert assess_risk(danger).level == "danger"
    expired = run("소비기한 2026.11.01까지")
    assert assess_risk(expired).level == "danger"
    caution = run("소비기한 2026.11.12까지")
    assert assess_risk(caution).level == "caution"
    unknown = run("원재료명: 설탕")
    assert assess_risk(unknown).level == "unknown"
    ok = run("소비기한 2026.12.20까지")
    assert assess_risk(ok).level == "ok"
