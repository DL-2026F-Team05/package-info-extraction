# 정보 추출 (AI 3) 입출력

AI 2 레포의 [`docs/interface.md`](https://github.com/DL-2026F-Team05/package-ocr-preprocessing/blob/main/docs/interface.md) 3번에 적힌 약속을 그대로 따릅니다. 이 문서는 그 약속을 자세히 적은 것입니다.

## 전체 흐름

```
사진 → run_pipeline() (AI 2) ─ result["ocr"] → extract() (AI 3) → tts() (AI 4)
```

## 1. 함수

```python
from extraction import extract

def extract(ocr_result, user_allergens: list[str] | None = None, today=None) -> dict:
    ...
```

- `ocr_result`: 아래 형식 중 무엇이든 그대로 넣으면 됩니다.

| 어디서 | 형식 |
|---|---|
| AI 1 `ocr()` / AI 2 `run_pipeline()` | `[{"text", "box": [[x, y] × 4], "score"}]` 또는 `run_pipeline` 결과 dict 전체 (`quality`도 같이 넘어감) |
| food-package-ocr README | `{"image", "lines": [{"text", "box": [x1, y1, x2, y2], "confidence"}]}` |
| AI 4 (AI_tts_web) | `{"boxes": [{"text", "conf", "poly", "region"}]}` 또는 `list[TextBox]` |
| OCR 엔진 원본 | EasyOCR `readtext` 결과, PaddleOCR `ocr` 결과 |
| 글자만 | `"소비기한 2026.12.15까지\n밀, 대두, 우유 함유"` (줄바꿈 = 한 줄) |

- `user_allergens`: 사용자의 알레르기. `"우유"`, `"계란"`, `"milk"`, `"밀가루"`처럼 써도 표준 이름(우유, 알류, 우유, 밀)으로 바꿔서 맞춥니다.
- `today`: 소비기한 계산 기준 날짜 (`"2026-11-17"` 또는 `date`). 없으면 한국 시간 오늘.

## 2. 출력

AI 2 문서의 출력 예시 키(`product_name`, `expiry.raw/date/days_left/expired`, `allergens.found/warning`, `cautions`, `priority`)는 이름과 뜻이 같습니다. 나머지 키는 더한 것입니다.

```json
{
  "product_name": "새우맛 스낵",
  "expiry": {
    "raw": "2026.12.15", "date": "2026-12-15", "days_left": 40, "expired": false,
    "kind": "use_by", "kind_label": "소비기한", "status": "ok", "status_label": "남음", "conf": 0.94
  },
  "allergens": {
    "found": ["밀", "대두", "우유"], "warning": ["우유"],
    "may_contain": ["땅콩"], "warning_may": [], "high_risk": ["밀", "대두", "우유", "땅콩"], "user": ["우유"], "conf": 0.88
  },
  "cautions": ["이 제품은 땅콩을 사용한 제품과 같은 제조시설"],
  "risk": {"level": "danger", "label": "위험", "reasons": ["allergen_contains"]},
  "priority": ["allergens", "expiry", "product_name", "cautions"],
  "quality": null,
  "fields": { "...": "AI 4 형식(Fields) 전체" }
}
```

| 키 | 뜻 |
|---|---|
| `expiry.date` | `YYYY-MM-DD`로 정규화한 날짜. 월까지만 적혀 있으면 그 달 마지막 날 |
| `expiry.kind` | `use_by` 소비기한, `sell_by` 유통기한, `best_before` 품질유지기한, `unknown` 종류 모름 |
| `expiry.status` | `ok`, `soon`(3일 이하), `expired`, `elsewhere`(다른 면에 표기), `unknown`(판단 보류) |
| `expiry.expired` | 지났으면 `true`. **판단 보류·다른 면 표기면 `null`** (모르는 것을 '안 지났다'고 하지 않음) |
| `allergens.found` | 함유 성분 (표시란 + 원재료명 속 성분). 이름은 표시 대상 19종의 표준 이름 |
| `allergens.may_contain` | 같은 제조시설 등 혼입 가능 성분. 함유에 있는 성분은 빠짐 |
| `allergens.warning` / `warning_may` | 사용자 알레르기와 겹치는 함유 / 혼입 가능 성분 |
| `cautions` | 혼입 안내 문장, 보관·주의사항 |
| `risk.level` | `danger` 위험, `caution` 주의, `unknown` 확인 필요, `ok` 특이사항 없음 (아래 4번) |
| `priority` | 음성으로 읽을 순서. 위험한 것이 앞에 옴 |
| `quality` | AI 2 `run_pipeline` 결과를 통째로 넣었으면 그 `quality`를 그대로 넘김 (`retake`, `advice`) |
| `*.conf` | 신뢰도 0~1. 낮으면 "틀릴 수 있어요" 안내에 씀 |

## 3. AI 4 파이프라인 형식

AI_tts_web 의 `packreader/pipeline.py`가 부르는 모양과 같습니다.

```python
from extraction import extract_fields, assess_risk
fields = extract_fields(boxes, profile, today, cfg)   # boxes: list[TextBox] → Fields
risk = assess_risk(fields)                            # → Risk
```

`Fields`, `Risk`, `TextBox`, `UserProfile`은 AI_tts_web `packreader/schema.py`와 같은 이름·같은 필드입니다. AI_tts_web 과 같이 있으면 원본 클래스를 그대로 쓰고, 이 레포만 있으면 `extraction/_standalone/`의 사본을 씁니다.

## 4. 위험 판단

| level | reasons |
|---|---|
| `danger` | `allergen_contains` 내 알레르기 함유, `expired` 소비기한 지남 |
| `caution` | `allergen_may` 내 알레르기 혼입 가능, `expiry_soon` 3일 이하 |
| `unknown` | `expiry_unknown` 기한 판단 보류, `expiry_elsewhere` 다른 면 표기, `text_unreadable` 글자를 거의 못 읽음, `allergen_unknown` 알레르기를 등록했는데 표시를 못 찾음, `allergen_uncertain` 알레르기 표시가 흐리거나 잘려서 내 성분이 없다고 단정할 수 없음 |
| `ok` | 해당 없음 |

판단 보류: 날짜 신뢰도가 0.6보다 낮으면(일·월을 가를 수 없는 05/06/2027, 같은 종류 기한이 서로 다른 날짜로 읽힘, 제조일보다 이른 소비기한, 3년 넘게 지났거나 10년 넘게 남은 날짜 포함) `status`를 `unknown`으로 두고 날짜를 말하지 않게 합니다.

`allergen_unknown`, `allergen_uncertain`은 사용자 알레르기를 알아야 판단할 수 있어서 `assess_risk(fields, profile)`처럼 profile 을 같이 넘길 때만 나옵니다. `extract()`는 항상 넘깁니다.
