"""Уклоны канализации и водопровода.

Пороги в конфиге — эвристика: пункты СП 30.13330 здесь не сверены по тексту редакции.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

HEIGHT_RE = re.compile(r"(?:ур\.?\s*)?([+\-−])?\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE)
SLOPE_RE = re.compile(r"i\s*=\s*(\d+(?:[.,]\d+)?)|(\d+(?:[.,]\d+)?)\s*‰", re.IGNORECASE)

DEFAULT_LAYERS = {
    "sewer": ["к1", "к2", "канализация"],
    "water": ["в1", "в2", "т3", "тз", "тш", "водопровод"],
}


def slope_value(dz: float, length: float) -> float | None:
    if length <= 1e-9:
        return None
    return dz / length


def _layer_kind(name: str, layers: dict) -> str | None:
    hay = (name or "").casefold().replace("ё", "е")
    for kind, names in layers.items():
        if hay in names or any(token in hay for token in names):
            return kind
    return None


def _parse_height(text: str) -> float | None:
    match = HEIGHT_RE.search(text.replace(" ", ""))
    if not match:
        return None
    sign = -1 if match.group(1) in {"-", "−"} else 1
    return sign * float(match.group(2).replace(",", "."))


def _parse_label(text: str) -> float | None:
    match = SLOPE_RE.search(text.replace(" ", ""))
    if not match:
        return None
    raw = match.group(1) or match.group(2)
    value = float(raw.replace(",", "."))
    if match.group(2):
        value /= 1000.0
    return value


def analyze_segments(segments: list[dict], rules: dict | None = None) -> list[dict]:
    rules = rules or {}
    findings = []
    for seg in segments:
        kind = seg["kind"]
        if seg.get("z1") is None or seg.get("z2") is None:
            findings.append({**seg, "status": "недостаточно данных", "slope": None, "message": "Нет высот концов участка."})
            continue
        dz = float(seg["z1"]) - float(seg["z2"])
        slope = slope_value(dz, float(seg["length"]))
        labeled = seg.get("labeled")
        messages = []
        status = "ok"
        if labeled is not None and slope is not None and abs(abs(labeled) - abs(slope)) > 0.002:
            status = "warning"
            messages.append(f"Подпись уклона {labeled} не совпадает с расчётом {slope:.4f}.")
        spec = rules.get(kind) or {}
        minimum = spec.get("min")
        maximum = spec.get("max")
        if kind == "sewer":
            if slope is not None and slope < -1e-6:
                status = "error"
                messages.append("Обратный уклон канализации.")
            elif slope is not None and abs(slope) <= 1e-9:
                status = "error"
                messages.append("Нулевой уклон канализации.")
            elif minimum is not None and slope is not None and 0 <= slope < float(minimum) - 1e-9:
                status = "warning"
                messages.append(f"Уклон {slope:.4f} меньше минимума {minimum}.")
            elif maximum is not None and slope is not None and slope > float(maximum) + 1e-9:
                status = "warning"
                messages.append(f"Уклон {slope:.4f} больше максимума {maximum}.")
        if kind == "water" and slope is not None:
            if abs(slope) <= 1e-9 and spec.get("warn_zero", True):
                status = "warning"
                messages.append("Нулевой уклон водопровода.")
            if minimum is not None and abs(slope) + 1e-12 < float(minimum):
                status = "warning"
                messages.append(f"Уклон водопровода {abs(slope):.4f} меньше {minimum}.")
            if maximum is not None and abs(slope) > float(maximum) + 1e-12:
                status = "warning"
                messages.append(f"Уклон водопровода {abs(slope):.4f} больше {maximum}.")
        findings.append({**seg, "status": status, "slope": slope, "message": " ".join(messages)})
    return findings


def segments_from_dxf(doc, layers: dict | None = None, unit_to_m: float = 0.001) -> list[dict]:
    layers = layers or DEFAULT_LAYERS
    texts = []
    for entity in doc.modelspace():
        if entity.dxftype() in {"TEXT", "MTEXT"}:
            content = entity.plain_text() if entity.dxftype() == "MTEXT" else str(entity.dxf.text or "")
            ins = entity.dxf.insert
            texts.append((float(ins.x), float(ins.y), content))
    rows = []
    for entity in doc.modelspace():
        kind = _layer_kind(str(entity.dxf.layer or ""), layers)
        if not kind or entity.dxftype() != "LINE":
            continue
        start, end = entity.dxf.start, entity.dxf.end
        length = math.hypot(end.x - start.x, end.y - start.y) * unit_to_m
        z1 = float(start.z) if abs(float(getattr(start, "z", 0) or 0)) > 1e-9 else None
        z2 = float(end.z) if abs(float(getattr(end, "z", 0) or 0)) > 1e-9 else None
        if z1 is None:
            z1 = _near_height(texts, start.x, start.y)
        if z2 is None:
            z2 = _near_height(texts, end.x, end.y)
        labeled = None
        for x, y, content in texts:
            if min(math.hypot(x - start.x, y - start.y), math.hypot(x - end.x, y - end.y)) < 30:
                labeled = _parse_label(content)
                if labeled is not None:
                    break
        rows.append({
            "kind": kind,
            "layer": str(entity.dxf.layer),
            "length": length,
            "z1": z1,
            "z2": z2,
            "labeled": labeled,
            "handle": str(entity.dxf.handle),
        })
    return rows


def slopes_cli(argv: list[str]) -> int:
    import argparse
    import json

    import ezdxf

    parser = argparse.ArgumentParser(prog="gost-check slopes")
    parser.add_argument("input")
    args = parser.parse_args(argv)
    doc = ezdxf.readfile(args.input)
    rows = analyze_segments(segments_from_dxf(doc))
    print(json.dumps(rows, ensure_ascii=False, default=str))
    return 0


def _near_height(texts, x, y, limit=30.0):
    best = None
    best_dist = limit
    for tx, ty, content in texts:
        dist = math.hypot(tx - x, ty - y)
        if dist <= best_dist:
            height = _parse_height(content)
            if height is not None:
                best = height
                best_dist = dist
    return best
