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

from ._deps import Config, UserProfile
from .adapters import load
from .allergens import ALLERGENS, find_allergens_ocr
from .api import analyze
from .dates import parse_dates
from .fields import extract_fields
from .risk import assess_risk

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
            # 엉뚱한 이름을 말함: 이름을 말했는데 글자 절반 이상이 틀림 (브랜드 로고, 광고 문구 등)
            rec["name_wrong_spoken"] = bool(fields.product_name.value) and rec["name_cer"] > 0.5
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
            "wrong_spoken_rate": round(sum(r["name_wrong_spoken"] for r in named) / len(named), 4),
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
        L += ["## 제품명", "", "| 완전 일치 | 평균 CER | 엉뚱한 이름을 말함 |", "|---|---|---|",
              f"| {p['exact']} | {p['cer_mean']} | {p['wrong_spoken_rate']} |", ""]
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


# ------------------------------------------------------------------ 기준값별 위험 놓침률·보류율
THRESHOLDS = tuple(round(i * 0.05, 2) for i in range(20))  # 0.00 ~ 0.95
_ABSTAIN_REASONS = ("allergen_unknown", "allergen_uncertain", "text_unreadable")


def sweep(ocr_files: list[Path], truth: dict[str, dict], today: dt.date, thresholds=THRESHOLDS) -> list[dict]:
    """신뢰도 기준값(소비기한 expiry_min_conf, 알레르기 allergen_min_conf 를 같은 값으로)을 바꿔 가며 잰다.

    위험한 경우 (놓치면 안 됨)
    - 소비기한: 정답 기한 다음 날을 '오늘'로 놓았을 때 '지났다'고 말하는지 (지났는데 괜찮다고 하면 놓침)
    - 알레르기: 정답 함유 성분 하나를 내 알레르기로 등록했을 때 '들어 있다'고 말하는지
      (혼입 가능으로 말하면 '약하게 말함', 아무 경고가 없으면 놓침)
    안전한 경우 (경고하면 안 됨)
    - 알레르기: 함유·혼입 어디에도 없는 성분을 등록했을 때 (경고하면 헛경보)
    보류: '확인 필요·다시 찍어 주세요'로 넘긴 경우. 기준값을 높이면 놓침은 줄고 보류는 는다.
    """
    items = []
    for f in ocr_files:
        inp = load(f)
        pid = product_of(f.name) or product_of(inp.image or "")
        if pid in truth:
            items.append((truth[pid], inp.boxes))

    # 알레르기 추출 결과는 기준값과 상관없어서 한 번만 계산한다
    allergen_cases = []  # (정답 종류, 추출 결과, 프로필)
    for tr, boxes in items:
        if "allergen" not in tr:
            continue
        gt_c, gt_any = set(tr["allergen"]), set(tr["allergen"]) | set(tr.get("may", []))
        for a in ALLERGENS:
            if a in gt_any and a not in gt_c:
                continue  # 혼입 가능 성분은 '위험/안전' 어느 쪽으로도 세지 않는다
            prof = UserProfile(allergens=[a])
            allergen_cases.append(("danger" if a in gt_c else "safe", a, extract_fields(boxes, prof, today, Config()), prof))

    rows = []
    for t in thresholds:
        cfg = Config(expiry_min_conf=t)
        c = defaultdict(int)
        for tr, boxes in items:
            if not tr.get("expiry"):
                continue
            f = extract_fields(boxes, None, tr["expiry"] + dt.timedelta(days=1), cfg)
            c["danger"] += 1
            c["date_cases"] += 1
            if f.expiry.status in ("unknown", "elsewhere") or f.expiry.date is None:
                c["abstain"] += 1
            else:
                c["missed" if f.expiry.status != "expired" else "caught"] += 1
                c["wrong_date"] += f.expiry.date != tr["expiry"]
        for kind, a, f, prof in allergen_cases:
            r = assess_risk(f, prof, allergen_min_conf=t)
            hit, may = a in f.allergens.user_hits, a in f.allergens.user_may_hits
            abstain = any(x in r.reasons for x in _ABSTAIN_REASONS)
            if kind == "danger":
                c["danger"] += 1
                c["caught" if hit else "under" if may else "abstain" if abstain else "missed"] += 1
            else:
                c["safe"] += 1
                c["false_alarm" if hit or may else "abstain" if abstain else "safe_ok"] += 1
        total = c["danger"] + c["safe"]
        rows.append({
            "threshold": t,
            "miss_rate": round(c["missed"] / c["danger"], 4) if c["danger"] else None,
            "under_warned_rate": round(c["under"] / c["danger"], 4) if c["danger"] else None,
            "abstain_rate": round(c["abstain"] / total, 4) if total else None,
            "wrong_date_spoken_rate": round(c["wrong_date"] / c["date_cases"], 4) if c["date_cases"] else None,
            "false_alarm_rate": round(c["false_alarm"] / c["safe"], 4) if c["safe"] else None,
            "danger_cases": c["danger"], "safe_cases": c["safe"],
        })
    return rows


def sweep_markdown(rows: list[dict], current: float = 0.6) -> str:
    L = ["## 기준값별 위험 놓침률·보류율", "",
         "기준값보다 신뢰도가 낮으면 판단을 보류한다 (소비기한·알레르기 같은 값). 지금 기본값은 "
         f"{current}.", "",
         "| 기준값 | 위험 놓침률 | 약하게 말함 | 보류율 | 틀린 날짜를 말함 | 헛경보 |", "|---|---|---|---|---|---|"]
    for r in rows:
        mark = " **(현재)**" if abs(r["threshold"] - current) < 1e-9 else ""
        L.append(f"| {r['threshold']:.2f}{mark} | {r['miss_rate']} | {r['under_warned_rate']} | {r['abstain_rate']} | "
                 f"{r['wrong_date_spoken_rate']} | {r['false_alarm_rate']} |")
    n = rows[0] if rows else {}
    L += ["", f"위험한 경우 {n.get('danger_cases', 0)}개, 안전한 경우 {n.get('safe_cases', 0)}개.", ""]
    return "\n".join(L)


def plot_sweep(rows: list[dict], path: Path, current: float = 0.6) -> bool:
    """위험 놓침률·보류율 그래프 (보고서·발표용). matplotlib 이 없으면 건너뛴다."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    xs = [r["threshold"] for r in rows]
    fig, ax = plt.subplots(figsize=(6, 3.6), dpi=150)
    ax.plot(xs, [r["miss_rate"] or 0 for r in rows], marker="o", label="miss rate (said safe)")
    ax.plot(xs, [r["abstain_rate"] or 0 for r in rows], marker="s", label="abstain rate (asked to retake)")
    ax.plot(xs, [r["wrong_date_spoken_rate"] or 0 for r in rows], ls="--", label="wrong date spoken")
    ax.axvline(current, color="gray", lw=1, ls=":")
    ax.text(current + 0.01, 0.97, f"current {current}", color="gray", fontsize=8, va="top")
    ax.set_xlabel("confidence threshold")
    ax.set_ylabel("rate")
    ax.set_ylim(0, 1.05)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    return True


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description="정보 추출 평가 (저장된 OCR 결과 + 정답 라벨)")
    ap.add_argument("--ocr-dir", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--today", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--sweep", action="store_true", help="기준값별 위험 놓침률·보류율 표와 그래프도 만들기")
    args = ap.parse_args(argv)
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    files = sorted(p for p in Path(args.ocr_dir).glob("*.json"))
    records, rep = evaluate(files, load_labels(Path(args.labels)), today, Config())
    md = to_markdown(rep, f"{args.ocr_dir} (기준일 {today})")
    rows = []
    if args.sweep:
        rows = sweep(files, load_labels(Path(args.labels)), today)
        rep["sweep"] = rows
        md += "\n" + sweep_markdown(rows)
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
        if rows:
            with (out / "sweep.csv").open("w", encoding="utf-8-sig", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
            plot_sweep(rows, out / "sweep.png")
    return rep


if __name__ == "__main__":
    main()
