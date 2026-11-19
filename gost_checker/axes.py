"""Координационные оси и цепочки размеров. Буквы и диаметр кружка — эвристика, пункт ГОСТ не сверен."""

from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path

# Часто исключаемые буквы в марках осей. Список не сверен по тексту ГОСТ 21.101 / 21.501.
EXCLUDED = set("ЁЗЙОХЦЧЩЪЫЬ")
LETTERS = [ch for ch in "АБВГДЕЖЗИКЛМНПРСТУФХЦЧШЩЭЮЯ" if ch not in EXCLUDED]
CIRCLE_MM = 8.0


def cluster(values: list[float], tol: float) -> list[float]:
    if not values:
        return []
    ordered = sorted(values)
    groups = [[ordered[0]]]
    for value in ordered[1:]:
        if value - groups[-1][-1] <= tol:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [sum(group) / len(group) for group in groups]


def marks(count: int) -> list[str]:
    names = []
    alphabet = LETTERS or ["А"]
    index = 0
    while len(names) < count:
        cycle = index // len(alphabet)
        letter = alphabet[index % len(alphabet)]
        names.append(letter if cycle == 0 else f"{letter}{cycle}")
        index += 1
    return names


def grid_from_points(points: list[tuple[float, float]], tol: float = 50.0) -> dict | None:
    if len(points) < 2:
        return None
    xs = cluster([p[0] for p in points], tol)
    ys = cluster([p[1] for p in points], tol)
    if len(xs) < 2 or len(ys) < 2:
        return None
    return {"xs": xs, "ys": ys, "numbers": [str(i + 1) for i in range(len(xs))], "letters": marks(len(ys))}


def axes_cli(argv: list[str]) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(prog="gost-check axes")
    parser.add_argument("input")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    print(json.dumps({k: v for k, v in build_axes(Path(args.input), Path(args.out)).items() if k != "original_unchanged"}, ensure_ascii=False))
    return 0


def build_axes(src: Path, dest: Path, tol: float = 50.0) -> dict:
    import ezdxf

    original = Path(src).read_bytes()
    doc = ezdxf.readfile(str(src))
    points = []
    for entity in doc.modelspace():
        layer = str(entity.dxf.layer or "").casefold()
        if entity.dxftype() == "INSERT" and any(token in layer or token in str(entity.dxf.name).casefold() for token in ("колон", "col")):
            ins = entity.dxf.insert
            points.append((float(ins.x), float(ins.y)))
        elif entity.dxftype() == "CIRCLE" and "колон" in layer:
            center = entity.dxf.center
            points.append((float(center.x), float(center.y)))
    grid = grid_from_points(points, tol)
    if grid is None:
        return {"ok": False, "message": "Сетка осей не построена.", "original_unchanged": Path(src).read_bytes() == original}
    if "АВТО_ОСИ" not in doc.layers:
        doc.layers.add("АВТО_ОСИ")
    msp = doc.modelspace()
    y0, y1 = min(grid["ys"]) - 400, max(grid["ys"]) + 400
    x0, x1 = min(grid["xs"]) - 400, max(grid["xs"]) + 400
    for x, number in zip(grid["xs"], grid["numbers"]):
        msp.add_line((x, y0), (x, y1), dxfattribs={"layer": "АВТО_ОСИ"})
        msp.add_circle((x, y0 - 200), CIRCLE_MM, dxfattribs={"layer": "АВТО_ОСИ"})
        msp.add_text(number, dxfattribs={"insert": (x - 2, y0 - 204), "height": 5, "layer": "АВТО_ОСИ"})
    for y, letter in zip(grid["ys"], grid["letters"]):
        msp.add_line((x0, y), (x1, y), dxfattribs={"layer": "АВТО_ОСИ"})
        msp.add_circle((x0 - 200, y), CIRCLE_MM, dxfattribs={"layer": "АВТО_ОСИ"})
        msp.add_text(letter, dxfattribs={"insert": (x0 - 204, y - 2), "height": 5, "layer": "АВТО_ОСИ"})
    if "АВТО_РАЗМЕРЫ" not in doc.layers:
        doc.layers.add("АВТО_РАЗМЕРЫ")
    chain = 0.0
    ys = grid["ys"]
    base_y = min(ys) - 800
    for left, right in zip(grid["xs"], grid["xs"][1:]):
        dim = msp.add_linear_dim(base=(left, base_y), p1=(left, min(ys)), p2=(right, min(ys)), dxfattribs={"layer": "АВТО_РАЗМЕРЫ"})
        dim.render()
        chain += abs(right - left)
    overall = abs(grid["xs"][-1] - grid["xs"][0])
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(str(dest))
    return {
        "ok": True,
        "numbers": grid["numbers"],
        "letters": grid["letters"],
        "chain": chain,
        "overall": overall,
        "draft": "черновик, требует проверки",
        "original_unchanged": Path(src).read_bytes() == original,
        "note": "Буквы и диаметр кружка 8 мм — эвристика, пункт ГОСТ 21.101 не сверен.",
    }
