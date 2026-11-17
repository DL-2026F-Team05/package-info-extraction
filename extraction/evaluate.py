"""정보 추출 평가: OCR 결과 JSON + 정답 라벨 → 항목별 정확도, F1, 위험 오분류율.  담당: 이서영

OCR 을 다시 돌리지 않고 저장된 OCR 결과만으로 AI 3 모듈의 성능을 잰다.
(사진부터 끝까지 재는 전체 평가는 AI_tts_web 의 evaluation/run_eval.py)

    python -m extraction.evaluate \\
        --ocr-dir data/samples/ai2_week2 --labels data/labels/labels.csv \\
        --today 2026-11-17 --out results/ai3_week2

정답 라벨 (package-ocr-preprocessing data/labels/labels.csv 형식, 제품마다 한 번)
    product_id,region,text
    p01,name,매운새우깡
    p01,allergen,"밀, 새우, 대두, 우유, 토마토, 게, 돼지고기, 쇠고기 함유"
    p01,expiry,2027.04.14
    p01,may,"닭고기, 오징어, 땅콩, 계란, 잣, 조개류"      ← 있으면 혼입 가능도 잰다
사진 파일 이름의 앞부분(p01, P001)이 product_id 다.

지표
- 제품명: 완전 일치율, 글자 오류율(CER)
- 소비기한: 날짜 정확도, 판단 보류율, 틀린 날짜를 말한 비율 (보류하지 않고 틀림 = 가장 위험)
- 알레르기 함유·혼입: micro/macro Precision·Recall·F1
- 위험 오분류율: 정답 함유 성분 하나하나를 '내 알레르기'로 등록했다고 보고
  · 놓침: 위험(함유)으로도 주의(혼입)로도 말하지 않음
  · 약하게 말함: 함유인데 혼입 가능으로 말함
  · 헛경보: 들어 있지 않은 성분을 함유라고 말함
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from ._deps import Config
from .adapters import load
from .allergens import ALLERGENS, find_allergens_ocr
from .api import analyze
from .dates import parse_dates

_PID = re.compile(r"(?:^|_)([pP]\d{2,4})(?=_|\.|$)")
_COND = re.compile(r"_(c\d+)_", re.I)
_REGION_ALIASES = {
    "name": "name", "product_name": "name", "제품명": "name",
    "allergen": "allergen", "contains": "allergen", "알레르기": "allergen",
    "may": "may", "may_contain": "may", "혼입": "may",
    "expiry": "expiry", "date": "expiry", "소비기한": "expiry",
}


def _lev(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def cer(ref: str, hyp: str) -> float:
    r = unicodedata.normalize("NFC", ref or "").replace(" ", "")
    h = unicodedata.normalize("NFC", hyp or "").replace(" ", "")
    return _lev(r, h) / max(1, len(r))


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def load_labels(path: Path) -> dict[str, dict]:
    truth: dict[str, dict] = defaultdict(dict)
    with path.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            pid = row["product_id"].strip().lower()
            region = _REGION_ALIASES.get(row["region"].strip().lower(), row["region"].strip().lower())
            text = row["text"].strip()
            if region == "name":
                truth[pid]["name"] = text
            elif region in ("allergen", "may"):
                truth[pid][region] = [n for n, _ in find_allergens_ocr(text)]
            elif region == "expiry":
                found = parse_dates(text)
                truth[pid]["expiry"] = found[0][2] if found else None
    return dict(truth)


def product_of(name: str) -> str | None:
    m = _PID.search(Path(name).stem)
    return m.group(1).lower() if m else None


def evaluate(ocr_files: list[Path], truth: dict[str, dict], today: dt.date, cfg: Config) -> tuple[list[dict], dict]:
    records = []
    for f in ocr_files:
        inp = load(f)
        pid = product_of(f.name) or product_of(inp.image or "")
        if pid not in truth:
            continue
        t = truth[pid]
        fields, risk, _ = analyze(inp, None, today, cfg)
        e, a = fields.expiry, fields.allergens
        engine = f.name.split("_")[0] if "_" in f.name else ""
        cond = _COND.search(f.name)
        rec = {
            "file": f.name, "product_id": pid, "engine": inp.extra.get("engine", engine),
            "condition": cond.group(1).lower() if cond else "", "n_boxes": len(inp.boxes),
            "pred_name": fields.product_name.value or "", "pred_date": e.date.isoformat() if e.date else "",
            "pred_status": e.status, "pred_contains": ";".join(a.contains), "pred_may": ";".join(a.may_contain),
        }
        if "name" in t:
            rec["gt_name"] = t["name"]
            rec["name_exact"] = (fields.product_name.value or "").replace(" ", "") == t["name"].replace(" ", "")
            rec["name_cer"] = round(cer(t["name"], fields.product_name.value or ""), 4)
        if t.get("expiry"):
            rec["gt_date"] = t["expiry"].isoformat()
            rec["date_correct"] = e.date == t["expiry"]
            rec["abstained"] = e.status in ("unknown", "elsewhere")
            rec["wrong_date_spoken"] = not rec["abstained"] and e.date != t["expiry"]
        for key, pred in (("allergen", set(a.contains)), ("may", set(a.may_contain))):
            if key in t:
                gt = set(t[key])
                rec[f"gt_{key}"] = ";".join(sorted(gt))
                rec[f"{key}_tp"], rec[f"{key}_fp"], rec[f"{key}_fn"] = len(gt & pred), len(pred - gt), len(gt - pred)
        if "allergen" in t:
            gt_c, gt_m = set(t["allergen"]), set(t.get("may", []))
            rec["risk_missed"] = sorted(x for x in gt_c if x not in a.contains and x not in a.may_contain)
            rec["risk_under"] = sorted(x for x in gt_c if x in a.may_contain)
            rec["risk_false_alarm"] = sorted(x for x in a.contains if x not in gt_c and x not in gt_m)
            # 알레르기 표시를 하나도 못 찾았으면 '알레르기 표시를 찾지 못했어요'라고 말한다 → 조용히 놓친 게 아니다
            rec["risk_silent_missed"] = rec["risk_missed"] if (a.contains or a.may_contain) else []
        records.append(rec)
    return records, summarize(records)


def summarize(records: list[dict]) -> dict:
    rep: dict = {"n_images": len(records)}
    named = [r for r in records if "name_cer" in r]
    if named:
        rep["product_name"] = {
            "n": len(named),
            "exact": round(sum(r["name_exact"] for r in named) / len(named), 4),
            "cer_mean": round(sum(r["name_cer"] for r in named) / len(named), 4),
        }
    dated = [r for r in records if "date_correct" in r]
    if dated:
        rep["expiry"] = {
            "n": len(dated),
            "date_accuracy": round(sum(r["date_correct"] for r in dated) / len(dated), 4),
            "abstain_rate": round(sum(r["abstained"] for r in dated) / len(dated), 4),
            "wrong_date_spoken_rate": round(sum(r["wrong_date_spoken"] for r in dated) / len(dated), 4),
        }
    for key, title in (("allergen", "allergen_contains"), ("may", "allergen_may_contain")):
        rs = [r for r in records if f"{key}_tp" in r]
        if not rs:
            continue
        tp, fp, fn = (sum(r[f"{key}_{k}"] for r in rs) for k in ("tp", "fp", "fn"))
        p, rr, f1 = prf(tp, fp, fn)
        per = {x: [0, 0, 0] for x in ALLERGENS}
        for r in rs:
            gt = set(filter(None, r[f"gt_{key}"].split(";")))
            pr = set(filter(None, r[f"pred_{'contains' if key == 'allergen' else 'may'}"].split(";")))
            for x in ALLERGENS:
                per[x][0] += x in gt and x in pr
                per[x][1] += x in pr and x not in gt
                per[x][2] += x in gt and x not in pr
        f1s = [prf(*v)[2] for v in per.values() if sum(v)]
        rep[title] = {
            "n": len(rs), "tp": tp, "fp": fp, "fn": fn,
            "precision": round(p, 4), "recall": round(rr, 4), "f1": round(f1, 4),
            "macro_f1": round(sum(f1s) / len(f1s), 4) if f1s else 0.0,
        }
    risky = [r for r in records if "risk_missed" in r]
    if risky:
        total = sum(len(r["gt_allergen"].split(";")) for r in risky if r["gt_allergen"])
        missed = sum(len(r["risk_missed"]) for r in risky)
        under = sum(len(r["risk_under"]) for r in risky)
        false = sum(len(r["risk_false_alarm"]) for r in risky)
        silent = sum(len(r["risk_silent_missed"]) for r in risky)
        rep["risk"] = {
            "n_cases": total,
            "missed_rate": round(missed / total, 4) if total else None,
            "under_warned_rate": round(under / total, 4) if total else None,
            "misclassified_rate": round((missed + under) / total, 4) if total else None,
            "silent_missed_rate": round(silent / total, 4) if total else None,
            "false_alarms": false,
        }
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        groups[f"{r['engine']}/{r['condition']}"].append(r)
    rep["by_group"] = {}
    for g, rs in sorted(groups.items()):
        row: dict = {"n": len(rs)}
        d = [r for r in rs if "date_correct" in r]
        if d:
            row["date_accuracy"] = round(sum(r["date_correct"] for r in d) / len(d), 4)
        c = [r for r in rs if "allergen_tp" in r]
        if c:
            row["contains_f1"] = round(prf(*(sum(r[f"allergen_{k}"] for r in c) for k in ("tp", "fp", "fn")))[2], 4)
        rep["by_group"][g] = row
    return rep


def to_markdown(rep: dict, title: str) -> str:
    L = [f"# 정보 추출 평가: {title}", "", f"사진 {rep['n_images']}장", ""]
    if "product_name" in rep:
        p = rep["product_name"]
        L += ["## 제품명", "", "| 완전 일치 | 평균 CER |", "|---|---|", f"| {p['exact']} | {p['cer_mean']} |", ""]
    if "expiry" in rep:
        e = rep["expiry"]
        L += ["## 소비기한", "", "| 날짜 정확도 | 판단 보류율 | 틀린 날짜를 말한 비율 |", "|---|---|---|",
              f"| {e['date_accuracy']} | {e['abstain_rate']} | {e['wrong_date_spoken_rate']} |", ""]
    for key, name in (("allergen_contains", "알레르기 함유"), ("allergen_may_contain", "혼입 가능")):
        if key in rep:
            a = rep[key]
            L += [f"## {name}", "", "| Precision | Recall | F1 | macro F1 | TP / FP / FN |", "|---|---|---|---|---|",
                  f"| {a['precision']} | {a['recall']} | {a['f1']} | {a['macro_f1']} | {a['tp']} / {a['fp']} / {a['fn']} |", ""]
    if "risk" in rep:
        r = rep["risk"]
        L += ["## 위험 오분류 (정답 함유 성분을 내 알레르기로 등록했다고 볼 때)", "",
              "| 경우 수 | 놓침 | 조용히 놓침* | 약하게 말함(혼입으로) | 위험 오분류율 | 헛경보 수 |", "|---|---|---|---|---|---|",
              f"| {r['n_cases']} | {r['missed_rate']} | {r['silent_missed_rate']} | {r['under_warned_rate']} | {r['misclassified_rate']} | {r['false_alarms']} |", "",
              "\\* 조용히 놓침: 알레르기 정보를 일부 찾아서 '표시를 찾지 못했어요' 안내 없이 넘어간 경우", ""]
    if rep.get("by_group"):
        L += ["## OCR 엔진·촬영 조건별", "", "| 엔진/조건 | 사진 | 날짜 정확도 | 함유 F1 |", "|---|---|---|---|"]
        for g, row in rep["by_group"].items():
            L.append(f"| {g} | {row['n']} | {row.get('date_accuracy', '-')} | {row.get('contains_f1', '-')} |")
        L.append("")
    return "\n".join(L)


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description="정보 추출 평가 (저장된 OCR 결과 + 정답 라벨)")
    ap.add_argument("--ocr-dir", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--today", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    files = sorted(p for p in Path(args.ocr_dir).glob("*.json"))
    records, rep = evaluate(files, load_labels(Path(args.labels)), today, Config())
    md = to_markdown(rep, f"{args.ocr_dir} (기준일 {today})")
    print(md)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "summary.md").write_text(md, encoding="utf-8")
        keys = sorted({k for r in records for k in r})
        with (out / "per_image.csv").open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=keys)
            w.writeheader()
            for r in records:
                w.writerow({k: (";".join(v) if isinstance(v, list) else v) for k, v in r.items()})
    return rep


if __name__ == "__main__":
    main()
