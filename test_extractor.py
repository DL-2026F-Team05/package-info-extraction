"""AI 3 모듈 테스트 - 실제 패키지 표기를 흉내 낸 OCR 결과 샘플"""
import json
from info_extractor import InfoExtractor

TODAY = "2026-11-05"
ex = InfoExtractor("allergens.json")

CASES = [
    {
        "name": "1. 일반 과자 (표준 표기)",
        "user": ["땅콩"],
        "ocr": [
            {"text": "바삭 버터쿠키", "bbox": [40, 20, 400, 90], "conf": 0.97},
            {"text": "원재료명: 밀가루(밀:미국산), 설탕, 버터(우유), 전란액, 대두레시틴, 정제소금", "bbox": [20, 300, 600, 320], "conf": 0.91},
            {"text": "알레르기 유발물질: 밀, 우유, 알류, 대두 함유", "bbox": [20, 330, 500, 350], "conf": 0.93},
            {"text": "이 제품은 땅콩, 호두를 사용한 제품과 같은 제조시설에서 제조하고 있습니다.", "bbox": [20, 360, 600, 380], "conf": 0.9},
            {"text": "소비기한: 2027.03.15까지", "bbox": [20, 400, 300, 420], "conf": 0.95},
            {"text": "보관방법: 직사광선을 피하고 서늘한 곳에 보관하십시오.", "bbox": [20, 430, 500, 450], "conf": 0.9},
        ],
    },
    {
        "name": "2. 우유 (소비기한 경과, 날짜가 다음 줄, 우유류는 유통기한 표기 유예)",
        "user": [],
        "ocr": "서울 흰우유 1000ml\n유통기한\n26.11.02\n알레르기 성분: 우유 함유\n개봉 후 냉장보관하고 빨리 드십시오.",
    },
    {
        "name": "3. 윗면 표기 + OCR 숫자 오인식 (O→0, l→1)",
        "user": [],
        "ocr": "참치 김밥\n소비기한: 윗면 표기일까지\n2O26.ll.O7 14:00\n원재료명: 쌀, 참치(가다랑어), 마요네즈(대두유, 난황), 단무지",
    },
    {
        "name": "4. 수입품 (영문 월 표기, 제조일 함께 있음)",
        "user": ["wheat"],
        "ocr": "Choco Wafer\nINGREDIENTS: wheat flour, sugar, cocoa\n원재료명: 밀가루, 설탕, 코코아분말, 탈지분유\nMFD 12 MAY 2026\nEXP 12 MAY 2027",
    },
    {
        "name": "5. 흐린 글자 (알레르겐 오탈자, 저신뢰도)",
        "user": ["우유"],
        "ocr": [
            {"text": "딸기 요거트", "conf": 0.9},
            {"text": "원재료명: 원유, 탈지븐유, 딸기잼, 정제수", "conf": 0.55},
            {"text": "소비기한 2026.11.06", "conf": 0.88},
        ],
    },
    {
        "name": "6. 제조일로부터 기간 표기",
        "user": [],
        "ocr": "옛날 라면\n제조일자: 2026.09.01\n소비기한: 제조일로부터 8개월\n알레르기 유발물질: 밀, 대두, 쇠고기, 토마토 함유\n밀폐 포장 제품입니다",
    },
]

for c in CASES:
    r = ex.extract(c["ocr"], user_allergies=c["user"], today=TODAY)
    print("=" * 80)
    print(c["name"], "| 사용자 알레르기:", c["user"] or "없음")
    print("- 제품명 :", r["product_name"])
    print("- 소비기한:", r["expiry"] and (r["expiry"]["date"], r["expiry"]["format"], "conf", r["expiry"]["conf"]))
    print("- 제조일자:", r["mfg_date"] and r["mfg_date"]["date"])
    print("- 알레르겐:", [(a["label"], a["status"], a["matched"]) for a in r["allergens"]])
    print("- 위험판단:", {k: r["risk"][k] for k in ("level", "expiry_status", "days_left", "user_allergen_hits", "needs_recheck")})
    if r["notes"]:
        print("- 메모   :", r["notes"])
    print("- 음성안내:", r["tts_script"])

# 날짜 해석 단위 테스트
print("=" * 80)
print("날짜 형식 단위 테스트")
samples = ["2026.12.31", "2026-12-31", "2026/12/31", "2026년 12월 31일", "26.12.31", "20261231 까지",
           "261231", "2026.12", "31/12/2026", "12/31/2026", "05/06/2026", "31 DEC 2026", "DEC 31, 2026",
           "2026. 12. 31.", "2026.02.30"]
for s in samples:
    hits = ex._find_dates(s, anchored=True)
    print(f"  {s:<16} -> {[(h.value.isoformat(), h.fmt, '모호' if h.ambiguous else '') for h in hits] or '인식 실패'}")
