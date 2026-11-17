"""OCR 글자 정리와 줄 묶기 (혼자 실행할 때 쓰는 사본).

AI_tts_web 의 packreader/ocr/base.py 에 있는 같은 이름의 함수와 똑같이 동작하도록 옮겨 두었다.
AI_tts_web 과 합쳐서 실행하면 원본 함수를 쓴다.
"""

from __future__ import annotations

import re
from statistics import median

from .schema import TextBox


def poly_bbox(poly) -> tuple[float, float, float, float]:
    xs = [float(p[0]) for p in poly]
    ys = [float(p[1]) for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def box_height(box: TextBox) -> float:
    if not box.poly:
        return 0.0
    _, y1, _, y2 = poly_bbox(box.poly)
    return y2 - y1


def group_lines(boxes: list[TextBox]) -> list[list[TextBox]]:
    """같은 줄에 있는 박스끼리 묶는다 (위→아래, 줄 안에서는 왼쪽→오른쪽). 좌표가 없으면 박스 하나 = 한 줄."""
    if not boxes or any(not b.poly for b in boxes):
        return [[b] for b in boxes]
    items = []
    for b in boxes:
        x1, y1, x2, y2 = poly_bbox(b.poly)
        items.append(((y1 + y2) / 2, x1, max(1.0, y2 - y1), b))
    tol = 0.5 * median(h for _, _, h, _ in items)
    items.sort(key=lambda t: t[0])
    lines: list[list[tuple]] = []
    for it in items:
        if lines and abs(it[0] - lines[-1][0][0]) <= tol:
            lines[-1].append(it)
        else:
            lines.append([it])
    return [[it[3] for it in sorted(line, key=lambda t: t[1])] for line in lines]


# 숫자 옆의 O(영문 오) → 0, 숫자 사이의 l·I·| → 1  ('6Og' → '60g', '2O26.1l.2O' → '2026.11.20')
_O_AS_ZERO = re.compile(r"(?<=\d)[Oo]|(?<![A-Za-z])[Oo](?=[~\-.,]?\d)")  # 'SO2', 'No.3' 은 그대로
_L_AS_ONE = re.compile(r"(?<=[\d.])[lI|](?=[\d.])")


def clean_ocr_text(text: str) -> str:
    prev = None
    while prev != text:
        prev = text
        text = _O_AS_ZERO.sub("0", text)
        text = _L_AS_ONE.sub("1", text)
    return text.strip()
