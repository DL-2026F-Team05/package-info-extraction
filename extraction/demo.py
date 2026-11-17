"""모듈 연결 확인: 샘플 데이터로 AI 2 → AI 3 (→ AI 4) 를 이어서 돌린다.  담당: 이서영

    python -m extraction.demo                                   # AI 2 → AI 3
    PYTHONPATH=../AI_tts_web python -m extraction.demo          # AI 2 → AI 3 → AI 4 안내 문장까지
    python -m extraction.demo --out results/ai3_week2/demo.md

1. AI 2 run_pipeline 결과 JSON (data/samples/ai2_week2, 매운새우깡 실제 사진 8장)을
   package-ocr-preprocessing docs/interface.md 에 적힌 대로 extract(result["ocr"], user_allergens) 로 넣는다.
2. food-package-ocr README 의 형식({"image", "lines": [{"text", "box", "confidence"}]})도 같은 함수로 넣는다.
3. AI_tts_web(packreader)이 있으면 AI 3 결과(Fields, Risk)를 AI 4 speech.compose 에 그대로 넣어 안내 문장을 만든다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from ._deps import INTEGRATED, Config
from .api import analyze, extract

ROOT = Path(__file__).resolve().parent.parent
AI2_DIR = ROOT / "data" / "samples" / "ai2_week2"
USER_ALLERGENS = ["우유", "땅콩"]


def _speech(fields, risk, quality: dict | None, n_boxes: int, today: dt.date) -> str:
    """AI 4 안내 문장 (packreader 가 있을 때만)."""
    from packreader.schema import Quality, UserProfile
    from packreader.speech import compose

    q = Quality(retake=bool(quality and quality.get("retake")))
    return compose(fields, risk, q, UserProfile(allergens=USER_ALLERGENS), today, n_boxes, Config()).summary


def run(today: dt.date) -> list[tuple[str, list[str], list[list[str]]]]:
    head = ["사진", "제품명", "소비기한", "함유", "혼입 가능", "위험도", "읽는 순서"]
    if INTEGRATED:
        head.append("AI 4 안내 문장")
    sections = []

    rows = []
    for f in sorted(AI2_DIR.glob("*.json")):
        result = json.loads(f.read_text(encoding="utf-8"))  # AI 2 run_pipeline 출력 그대로
        info = extract(result["ocr"], USER_ALLERGENS, today)  # AI 2 문서의 사용법 그대로
        e, a = info["expiry"], info["allergens"]
        row = [f.stem, info["product_name"] or "-", f"{e['date'] or '-'} ({e['status_label']})",
               ", ".join(a["found"]) or "-", ", ".join(a["may_contain"]) or "-", info["risk"]["label"],
               " → ".join(info["priority"])]
        if INTEGRATED:
            fields, risk, inp = analyze(result, USER_ALLERGENS, today)
            row.append(_speech(fields, risk, result.get("quality"), len(inp.boxes), today))
        rows.append(row)
    sections.append(("1. AI 2 run_pipeline 결과 → AI 3 extract()", head, rows))

    team = {
        "image": "P004_1.jpg",
        "lines": [
            {"text": "제품명: 고소한 두유", "box": [10, 10, 300, 40], "confidence": 0.95},
            {"text": "대두, 우유 함유", "box": [10, 50, 200, 75], "confidence": 0.93},
            {"text": "2026.12.15 까지", "box": [10, 90, 200, 115], "confidence": 0.97},
        ],
    }
    info = extract(team, USER_ALLERGENS, today)
    e, a = info["expiry"], info["allergens"]
    row = [info["image"], info["product_name"], f"{e['date']} ({e['status_label']})", ", ".join(a["found"]),
           ", ".join(a["may_contain"]) or "-", info["risk"]["label"], " → ".join(info["priority"])]
    if INTEGRATED:
        fields, risk, inp = analyze(team, USER_ALLERGENS, today)
        row.append(_speech(fields, risk, None, len(inp.boxes), today))
    sections.append(("2. food-package-ocr 형식 입력", head, [row]))
    return sections


def to_markdown(sections, today: dt.date) -> str:
    out = [f"# AI 3 모듈 연결 확인 (기준일 {today}, 내 알레르기: {'·'.join(USER_ALLERGENS)})", ""]
    if not INTEGRATED:
        out += ["AI 4 안내 문장까지 보려면 AI_tts_web 을 받아 두고 `PYTHONPATH=../AI_tts_web python -m extraction.demo`", ""]
    for title, head, rows in sections:
        out += [f"## {title}", "", "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
        out += ["| " + " | ".join(str(c).replace("|", "/") for c in r) + " |" for r in rows]
        out.append("")
    return "\n".join(out)


def main(argv=None) -> str:
    ap = argparse.ArgumentParser(description="AI 2 → AI 3 (→ AI 4) 연결 확인")
    ap.add_argument("--today", default="2026-11-17")
    ap.add_argument("--out", default=None, help="마크다운으로 저장할 경로")
    args = ap.parse_args(argv)
    today = dt.date.fromisoformat(args.today)
    md = to_markdown(run(today), today)
    print(md)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(md, encoding="utf-8")
    return md


if __name__ == "__main__":
    main()
