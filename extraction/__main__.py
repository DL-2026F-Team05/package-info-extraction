"""정보 추출 모듈을 혼자 실행한다 (OCR 결과 JSON → 핵심 정보·위험 판단).  담당: 이서영

    python -m extraction data/samples/ai2_week2/paddle_p01_c0_1.json --allergy 우유 땅콩
    python -m extraction data/samples/ai2_week2 --today 2026-11-17 --out results/ai3_week2/extract
    python -m extraction --text "소비기한 26.11.20까지\n밀, 우유 함유" --allergy 우유

입력은 AI 2 run_pipeline JSON, OCR 줄 목록, food-package-ocr 형식, 글자 파일(.txt) 모두 된다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ._deps import Config
from .adapters import load, to_input
from .api import extract


def _inputs(paths: list[str]) -> list:
    out = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            out += [load(f) for f in sorted(path.glob("*.json"))]
        else:
            out.append(load(path))
    return out


def summary(info: dict) -> str:
    e, a, r = info["expiry"], info["allergens"], info["risk"]
    exp = f"{e['date']} ({e['kind_label']}, {e['status_label']})" if e["date"] else e["status_label"]
    return "\n".join([
        f"[{info['image'] or '입력'}] 위험도: {r['label']} ({', '.join(r['reasons']) or '-'})",
        f"  제품명   : {info['product_name'] or '-'} (신뢰도 {info['product_name_conf']})",
        f"  소비기한 : {exp}  원문='{e['raw'] or ''}'  신뢰도 {e['conf']}",
        f"  함유     : {', '.join(a['found']) or '-'}",
        f"  혼입 가능: {', '.join(a['may_contain']) or '-'}",
        f"  내 성분  : 함유 {', '.join(a['warning']) or '-'} / 혼입 {', '.join(a['warning_may']) or '-'}",
        f"  읽는 순서: {' → '.join(info['priority'])}",
    ])


def main(argv=None) -> list[dict]:
    ap = argparse.ArgumentParser(prog="python -m extraction", description="OCR 결과에서 핵심 정보 추출·위험 판단")
    ap.add_argument("inputs", nargs="*", help="OCR 결과 JSON 파일 또는 폴더")
    ap.add_argument("--text", help="OCR 대신 글자를 직접 넣기 (\\n 으로 줄 나눔)")
    ap.add_argument("--allergy", nargs="*", default=[], help="내 알레르기 성분 (예: 우유 땅콩)")
    ap.add_argument("--today", default=None, help="기준 날짜 YYYY-MM-DD (기본: 오늘, 한국 시간)")
    ap.add_argument("--json", action="store_true", help="전체 결과를 JSON 으로 출력")
    ap.add_argument("--out", default=None, help="결과 JSON 을 저장할 폴더")
    args = ap.parse_args(argv)

    if args.text:
        items = [to_input(args.text.replace("\\n", "\n"))]
    elif args.inputs:
        items = _inputs(args.inputs)
    else:
        ap.error("OCR 결과 파일이나 --text 를 넣어 주세요")

    results = []
    for inp in items:
        info = extract(inp, args.allergy, args.today, Config())
        results.append(info)
        print(json.dumps(info, ensure_ascii=False, indent=2) if args.json else summary(info) + "\n")
        if args.out:
            out = Path(args.out)
            out.mkdir(parents=True, exist_ok=True)
            (out / (Path(info["image"] or "text").stem + ".json")).write_text(
                json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    return results


if __name__ == "__main__":
    try:
        main()
    except (ValueError, FileNotFoundError) as exc:
        print(f"입력 오류: {exc}", file=sys.stderr)
        sys.exit(2)
