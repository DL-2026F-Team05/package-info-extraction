"""정보 추출 모듈 테스트 (명세 테스트 test_spec_ai4.py 외에 추가한 것).

- 날짜 정규화·판단 보류, OCR 오탈자를 견디는 알레르기 매칭, 표 형태 포장
- 팀원 OCR 결과 모양 맞추기 (AI 2 run_pipeline, food-package-ocr, EasyOCR·PaddleOCR 원본)
- 실제 사진 회귀 테스트: data/samples/ai2_week2 (AI 2 가 매운새우깡 사진 8장을 EasyOCR·PaddleOCR 로 읽은 결과)
"""

import datetime as dt
import json

import pytest

from extraction import (
    analyze, assess_risk, canonicalize_list, extract, extract_fields, find_allergens_ocr, ingredient_allergens,
    load, to_boxes,
)
from extraction._deps import Config, TextBox, UserProfile
from extraction.allergens import fuzzy_canonical
from extraction.evaluate import evaluate, load_labels

from conftest import ROOT

TODAY = dt.date(2026, 11, 10)
REAL = ROOT / "data" / "samples" / "ai2_week2"
LABELS = ROOT / "data" / "labels" / "labels.csv"
P01_CONTAINS = ["밀", "새우", "대두", "우유", "토마토", "게", "돼지고기", "쇠고기"]
P01_MAY = ["닭고기", "오징어", "땅콩", "알류", "잣", "조개류"]


def run(*lines, profile=None, conf=0.95):
    return extract_fields([TextBox(text=t, conf=conf) for t in lines], profile, TODAY, Config())


def box(text, x1, y1, x2, y2, conf=0.95):
    return TextBox(text=text, conf=conf, poly=[(x1, y1), (x2, y1), (x2, y2), (x1, y2)])


# ------------------------------------------------------------------ 날짜
@pytest.mark.parametrize(
    "line, date",
    [
        ("소비기한 26년 11월 20일까지", "2026-11-20"),
        ("BEST BEFORE 20 NOV 2026", "2026-11-20"),
        ("EXP NOV 20, 2026", "2026-11-20"),
        ("USE BY 20/11/2026", "2026-11-20"),
        ("EXP 11/20/2026", "2026-11-20"),
        ("소비기한 261120", "2026-11-20"),
        ("소비기한 2026.11", "2026-11-30"),
        ("소비기한 2026. 11. 20.", "2026-11-20"),
    ],
)
def test_more_date_formats(line, date):
    assert str(run(line).expiry.date) == date


def test_kind_from_english_keywords():
    assert run("BEST BEFORE 2026.12.01").expiry.kind == "best_before"
    assert run("EXP 2026.12.01").expiry.kind == "use_by"


def test_ambiguous_day_month_abstains():
    e = run("EXP 05/06/2027").expiry
    assert e.status == "unknown"  # 5월 6일인지 6월 5일인지 모르면 말하지 않는다


def test_compact_number_without_keyword_is_not_a_date():
    assert run("바코드 20261120 1234").expiry.date is None  # 키워드 없는 숫자는 로트번호·바코드일 수 있다


def test_until_without_keyword_is_use_by():
    e = run("2026.12.15 까지").expiry
    assert e.kind == "use_by" and e.status == "ok"


def test_plain_date_stays_unknown_kind():
    assert run("2026.12.24").expiry.kind == "unknown"  # '까지'도 키워드도 없으면 종류를 단정하지 않는다


def test_relative_without_manufacture_date_abstains():
    e = run("소비기한: 제조일로부터 12개월").expiry
    assert e.status == "unknown" and e.raw and e.date is None


def test_expiry_before_manufacture_abstains():
    assert run("제조일자 2026.12.01", "소비기한 2026.11.20").expiry.status == "unknown"


def test_elsewhere_then_separate_date():
    """실제 포장: 표에는 '후면표기일까지', 날짜는 다른 곳에 따로 찍힘"""
    e = run("소비기한", "후면표기일까지", "8801043036078", "2027.04.14까지").expiry
    assert e.kind == "use_by" and str(e.date) == "2027-04-14" and e.status == "ok"


def test_ocr_typo_in_keyword_and_digits():
    e = run("소비기힌 2O27.O4.l4까지").expiry
    assert e.kind == "use_by" and str(e.date) == "2027-04-14"


# ------------------------------------------------------------------ 알레르기
def test_fuzzy_names():
    assert fuzzy_canonical("유유") == "우유"
    assert fuzzy_canonical("토미토") == "토마토"
    assert fuzzy_canonical("돼지고") == "돼지고기"
    assert fuzzy_canonical("홍한") == "조개류"
    assert fuzzy_canonical("함유") is None
    assert fuzzy_canonical("두유") is None  # 두유는 오탈자가 아니라 대두 원재료


def test_soy_milk_is_soy_not_milk():
    assert [n for n, _ in find_allergens_ocr("두유, 계란")] == ["대두", "알류"]


def test_merged_tokens_split():
    assert [n for n, _ in find_allergens_ocr("밀새우 대두")] == ["밀", "새우", "대두"]


def test_real_ocr_contains_line_with_typos():
    a = run("밀 새우 대두 유유 토미토 게 돼지고 함유").allergens
    assert a.contains == ["밀", "새우", "대두", "우유", "토마토", "게", "돼지고기"]
    assert a.conf < 0.95  # 오탈자로 맞춘 것이 있으면 신뢰도를 낮춘다


def test_contains_word_on_its_own_line():
    a = run("밀,새우,대두,우유,토마토,게,돼지고기,쇠고기", "함유").allergens
    assert a.contains == P01_CONTAINS


def test_contains_word_before_list():
    assert run("함유 밀,새우,대두").allergens.contains == ["밀", "새우", "대두"]


def test_list_line_without_contains_word():
    assert run("밀, 대두, 우유, 토마토, 게").allergens.contains == ["밀", "대두", "우유", "토마토", "게"]


def test_non_allergen_contains_line():
    a = run("원재료명: 정제수, 설탕", "카페인 함유").allergens
    assert a.contains == []


def test_facility_sentence_with_bullet_and_no_this_product():
    a = run("·닭고기,오징어,땅콩,계란,잣, 조개류(굴,전복, 홍한",
            "포함)를 사용한 제품과 같은 시설에서 제조하고 있습니다").allergens
    assert a.may_contain == P01_MAY


def test_facility_sentence_items_after():
    a = run("같은 제조시설에서 땅콩, 호두를 사용한 제품을 생산하고 있습니다.").allergens
    assert a.may_contain == ["땅콩", "호두"]


def test_may_contain_english():
    assert run("May contain peanuts and pine nuts.").allergens.may_contain == ["땅콩", "잣"]


def test_facility_does_not_swallow_previous_field_lines():
    a = run("밀새우 대투 유유 토마토 게", "소비기한 후면 표기일까지 포장재질 폴리프로필렌",
            "닭고기 오징어 땅콩 계란", "포함틀 사용한 제품과 같은 시설에서 제조").allergens
    assert "우유" in a.contains and "우유" not in a.may_contain
    assert a.may_contain == ["닭고기", "오징어", "땅콩", "알류"]


def test_ingredient_inference_from_compound_names():
    assert ingredient_allergens("쉬림프크래커중국산소맥분, 옥수수전분") == ["새우", "밀"]
    assert set(ingredient_allergens("전지분유, 양조간장, 난황")) == {"우유", "대두", "알류"}
    assert ingredient_allergens("정제수, 설탕, 식물성크림") == []


def test_profile_names_are_flexible():
    assert canonicalize_list(["milk", "계란", "밀가루", "두유", "모름"]) == ["우유", "알류", "밀", "대두"]


# ------------------------------------------------------------------ 표 형태 포장 (박스 좌표)
def test_table_layout_product_name_and_ingredients():
    boxes = [
        box("제품명", 10, 10, 70, 40), box("매운새우깡", 80, 10, 180, 40), box("식품유형", 300, 10, 380, 40),
        box("쉬림프크래커중국산소맥분,", 80, 50, 300, 75),
        box("원재료명", 10, 85, 70, 115), box("팜유,매운맛시즈닝", 80, 85, 260, 115),
        box("밀,새우,대두,우유", 80, 125, 260, 150), box("함유", 80, 155, 120, 180),
        box("소비기한", 10, 190, 70, 220), box("후면표기일까지", 80, 190, 200, 220),
    ]
    f = extract_fields(boxes, None, TODAY, Config())
    assert f.product_name.value == "매운새우깡"
    assert "쉬림프크래커" in f.ingredients.value and "팜유" in f.ingredients.value
    assert "함유" not in f.ingredients.value
    assert f.allergens.contains == ["밀", "새우", "대두", "우유"]
    assert f.expiry.status == "elsewhere"


# ------------------------------------------------------------------ 위험 판단
def test_registered_allergy_but_no_label_is_not_ok():
    f = run("소비기한 2026.12.20까지")
    assert assess_risk(f).level == "ok"  # 등록 안 함 (예전과 같음)
    r = assess_risk(f, UserProfile(allergens=["땅콩"]))
    assert r.level == "unknown" and "allergen_unknown" in r.reasons


def test_fuzzy_user_hit_is_still_danger():
    f = run("유유 함유", profile=UserProfile(allergens=["우유"]))
    assert assess_risk(f).level == "danger"


# ------------------------------------------------------------------ 입력 모양 맞추기
def test_adapters_accept_team_formats():
    ai2 = {"ocr": [{"text": "우유 함유", "box": [[0, 0], [10, 0], [10, 5], [0, 5]], "score": 0.9}],
           "quality": {"retake": False}}
    fpo = {"image": "P004_1.jpg", "lines": [{"text": "우유 함유", "box": [0, 0, 10, 5], "confidence": 0.9}]}
    easy = [([[0, 0], [10, 0], [10, 5], [0, 5]], "우유 함유", 0.9)]
    paddle = [[[[[0, 0], [10, 0], [10, 5], [0, 5]], ("우유 함유", 0.9)]]]
    for data in (ai2, fpo, easy, paddle, "우유 함유", [TextBox(text="우유 함유")]):
        boxes = to_boxes(data)
        assert boxes[0].text == "우유 함유"
    assert to_boxes(fpo)[0].poly[2] == (10.0, 5.0)
    with pytest.raises(ValueError):
        to_boxes({"nothing": 1})


def test_extract_dict_matches_ai2_interface():
    info = extract("제품명: 새우맛 스낵\n소비기한 2026.12.15까지\n밀, 대두, 우유 함유\n"
                   "이 제품은 땅콩을 사용한 제품과 같은 제조시설에서 제조", ["우유", "땅콩"], "2026-11-05")
    assert info["product_name"] == "새우맛 스낵"
    assert info["expiry"] | {"conf": None} == {
        "raw": "2026.12.15", "date": "2026-12-15", "kind": "use_by", "kind_label": "소비기한", "status": "ok",
        "status_label": "남음", "days_left": 40, "expired": False, "conf": None,
    }
    assert info["allergens"]["found"] == ["밀", "대두", "우유"]
    assert info["allergens"]["warning"] == ["우유"] and info["allergens"]["warning_may"] == ["땅콩"]
    assert info["risk"]["level"] == "danger"
    assert info["priority"] == ["allergens", "expiry", "product_name", "cautions"]  # AI 2 문서의 키 이름
    assert any("시설" in c for c in info["cautions"])
    json.dumps(info, ensure_ascii=False)  # 그대로 JSON 으로 넘길 수 있어야 한다


def test_expired_goes_before_product_name():
    info = extract("제품명: 우유\n소비기한 2026.11.01까지", [], "2026-11-05")
    assert info["priority"][:2] == ["expiry", "product_name"]
    assert info["expiry"]["expired"] is True


def test_ai2_quality_is_passed_through():
    info = extract({"ocr": [{"text": "흐림", "box": [0, 0, 1, 1], "score": 0.1}],
                    "quality": {"retake": True, "advice": "다시 찍어 주세요"}})
    assert info["quality"]["advice"] == "다시 찍어 주세요"  # AI 4 가 재촬영 안내를 먼저 읽을 수 있게 그대로 넘김


# ------------------------------------------------------------------ 실제 사진 회귀 테스트
@pytest.mark.parametrize("name", ["paddle_p01_c0_1.json", "paddle_p01_c6_L2.json"])
def test_real_paddle_photos_exact(name):
    fields, risk, inp = analyze(load(REAL / name), ["우유"], "2026-11-17")
    assert inp.source == "ai2"
    assert fields.allergens.contains == P01_CONTAINS
    assert fields.allergens.may_contain == P01_MAY
    assert str(fields.expiry.date) == "2027-04-14" and fields.expiry.kind == "use_by"
    assert risk.level == "danger"


def test_real_easyocr_photo_partial_but_no_false_alarm():
    fields, _, _ = analyze(load(REAL / "easyocr_p01_c0_1.json"), None, "2026-11-17")
    assert set(fields.allergens.contains) <= set(P01_CONTAINS)
    assert len(fields.allergens.contains) >= 6
    assert set(fields.allergens.may_contain) <= set(P01_MAY)


def test_real_photos_never_speak_wrong_date_or_false_allergen():
    records, rep = evaluate(sorted(REAL.glob("*.json")), load_labels(LABELS),
                            dt.date(2026, 11, 17), Config())
    assert len(records) == 8
    assert rep["expiry"]["wrong_date_spoken_rate"] == 0.0
    assert rep["allergen_contains"]["fp"] == 0 and rep["allergen_may_contain"]["fp"] == 0
    assert rep["allergen_contains"]["f1"] >= 0.7
    assert rep["risk"]["under_warned_rate"] == 0.0


def test_ai2_documented_usage():
    """package-ocr-preprocessing docs/interface.md: extract(result["ocr"], user_allergens)"""
    result = json.loads((REAL / "paddle_p01_c0_1.json").read_text(encoding="utf-8"))
    info = extract(result["ocr"], ["우유"], "2026-11-17")
    for key in ("product_name", "expiry", "allergens", "cautions", "priority"):
        assert key in info
    assert set(info["expiry"]) >= {"raw", "date", "days_left", "expired"}
    assert info["allergens"]["warning"] == ["우유"]


def test_cli_and_demo_run(capsys):
    from extraction.__main__ import main as cli
    from extraction.demo import main as demo

    out = cli([str(REAL / "paddle_p01_c0_1.json"), "--allergy", "우유", "--today", "2026-11-17"])
    assert out[0]["risk"]["level"] == "danger"
    md = demo([])
    assert "AI 2 run_pipeline" in md and "food-package-ocr" in md


# ------------------------------------------------------------------ 3주차: 위험 판단 예외 처리
def test_unreadable_photo_claims_nothing():
    info = extract([{"text": "흐림", "score": 0.1}, {"text": "ㅁㄴ", "score": 0.2}], ["우유"])
    assert info["product_name"] is None and info["allergens"]["found"] == []
    assert info["risk"]["level"] == "unknown" and "text_unreadable" in info["risk"]["reasons"]


def test_conflicting_same_kind_dates_abstain():
    """앞·뒤에 찍힌 소비기한 중 하나를 잘못 읽으면 어느 쪽인지 모른다 → 날짜를 말하지 않음"""
    e = run("소비기한 2026.12.01", "소비기한 2026.12.07").expiry
    assert e.status == "unknown"
    assert run("소비기한 2026.12.01", "소비기한 2026.12.01").expiry.status == "ok"  # 같은 날짜면 괜찮다
    assert run("유통기한 2026.12.01", "소비기한 2026.12.07").expiry.status == "ok"  # 종류가 다르면 소비기한 우선


def test_english_contains_and_may_contain_on_one_line():
    a = run("Contains: milk, soy. May contain peanuts.").allergens
    assert a.contains == ["우유", "대두"] and a.may_contain == ["땅콩"]


def test_milk_sell_by_date():
    e = run("유통기한 26.11.12").expiry  # 냉장 우유류는 2031년까지 유통기한 표기
    assert e.kind == "sell_by" and e.status == "soon"


def test_truncated_allergen_list_is_uncertain_for_user():
    f = run("알레르기 유발물질: 밀, 우유, 대두,", "소비기한 2026.12.10까지")
    assert f.allergens.contains == ["밀", "우유", "대두"]
    r = assess_risk(f, UserProfile(allergens=["땅콩"]))
    assert r.level == "unknown" and "allergen_uncertain" in r.reasons  # 잘린 뒤에 땅콩이 있을 수 있다
    mine = UserProfile(allergens=["우유"])
    f = run("알레르기 유발물질: 밀, 우유, 대두,", "소비기한 2026.12.10까지", profile=mine)
    assert assess_risk(f, mine).level == "danger"  # 보이는 성분은 그대로 위험


def test_blurry_allergen_line_is_uncertain_for_user():
    f = run("밀, 대두 함유", "소비기한 2026.12.10까지", conf=0.45)
    assert assess_risk(f, UserProfile(allergens=["땅콩"])).reasons[-1] == "allergen_uncertain"


# ------------------------------------------------------------------ 3주차: 제품명
def test_brand_logo_is_not_product_name():
    boxes = [box("농심", 10, 10, 210, 110), box("매운새우깡", 10, 120, 230, 190), box("새우를 넣어 만들어요", 10, 200, 300, 230)]
    assert extract_fields(boxes, None, TODAY, Config()).product_name.value == "매운새우깡"


def test_brand_with_ocr_extra_letter():
    boxes = [box("농심물", 10, 10, 210, 110), box("고소한 두유", 10, 120, 200, 180)]
    assert extract_fields(boxes, None, TODAY, Config()).product_name.value == "고소한 두유"


def test_small_text_is_not_guessed_as_name():
    boxes = [box("농심", 10, 10, 210, 110), box("골고루", 10, 130, 80, 160), box("영양정보", 10, 170, 200, 250)]
    assert extract_fields(boxes, None, TODAY, Config()).product_name.value is None  # 모르면 말하지 않는다


def test_product_label_with_ocr_typo():
    boxes = [box("제품생", 10, 10, 60, 40), box("매운새우", 70, 10, 160, 40), box("식품유형 과자", 300, 10, 420, 40)]
    assert extract_fields(boxes, None, TODAY, Config()).product_name.value == "매운새우"


# ------------------------------------------------------------------ 3주차: 기준값별 위험 놓침률·보류율
def test_threshold_sweep_tradeoff():
    from extraction.evaluate import sweep

    rows = sweep(sorted(REAL.glob("*.json")), load_labels(LABELS), dt.date(2026, 11, 17), thresholds=(0.0, 0.6, 0.95))
    lo, cur, hi = rows
    assert lo["danger_cases"] == 72 and lo["safe_cases"] == 40
    assert hi["miss_rate"] <= cur["miss_rate"] <= lo["miss_rate"]  # 기준을 높이면 놓침이 줄고
    assert hi["abstain_rate"] >= cur["abstain_rate"] >= lo["abstain_rate"]  # 보류가 는다
    assert cur["miss_rate"] == 0.0 and cur["wrong_date_spoken_rate"] == 0.0 and cur["false_alarm_rate"] == 0.0
