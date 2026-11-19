"""Выноски по кружкам пункта 9.

Номера уже стоящих кружков сохраняются. Новые получают номера в устойчивом порядке.
Полка ищется через проверку свободного места. Слой АВТО_ВЫНОСКИ. PDF не изменяется.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .markups import DEFAULT_LAYERS, circles_from_dxf, circles_from_pdf, parse_descriptions

LEADER_LAYERS = DEFAULT_LAYERS + ("примечания_метки", "примечания", "метки")
LAYER = "АВТО_ВЫНОСКИ"


def assign_numbers(points: list[dict]) -> list[dict]:
    """Одинаковый набор точек всегда получает те же номера, независимо от порядка входа."""
    existing = [int(item["number"]) for item in points if item.get("number")]
    nxt = max(existing, default=0) + 1
    ranked = sorted(points, key=lambda item: (-round(float(item["y"]), 3), round(float(item["x"]), 3)))
    assigned = []
    for item in ranked:
        copy = dict(item)
        if not copy.get("number"):
            copy["number"] = nxt
            nxt += 1
        assigned.append(copy)
    return sorted(assigned, key=lambda item: int(item["number"]))


def _shelf(doc, point, text: str) -> tuple[float, float]:
    from .clearance import suggest

    x, y = point
    try:
        slot = suggest(doc, (x, y), (x + 20.0, y), text)
    except Exception:
        slot = {}
    offset = 0.0
    if slot.get("fits"):
        offset = float(slot.get("offset") or 0.0)
    elif slot.get("alternatives"):
        offset = float(slot["alternatives"][0].get("offset") or 0.0)
    if abs(offset) < 1e-6:
        offset = 12.0
    return x + 15.0, y + offset


def write_leaders(src: Path, dest: Path | None = None, notes: str = "") -> dict:
    src = Path(src)
    before = hashlib.sha256(src.read_bytes()).hexdigest()
    parsed = parse_descriptions(notes)
    if src.suffix.lower() == ".pdf":
        circles = circles_from_pdf(src)
        items = assign_numbers([{"x": item["cx"], "y": item["cy"], "number": item.get("number"), "comment": item.get("comment") or ""} for item in circles])
        for item in items:
            item["text"] = parsed.get(item["number"]) or item.get("comment") or ""
        return {
            "check_only": True,
            "written": False,
            "leaders": items,
            "note": "PDF только читается, файл не изменяется.",
            "original_unchanged": hashlib.sha256(src.read_bytes()).hexdigest() == before,
        }
    import ezdxf

    doc = ezdxf.readfile(str(src))
    circles = circles_from_dxf(doc, LEADER_LAYERS)
    items = assign_numbers(
        [{"x": item["cx"], "y": item["cy"], "number": item.get("number"), "comment": item.get("comment") or ""} for item in circles]
    )
    if LAYER not in doc.layers:
        doc.layers.add(LAYER)
    msp = doc.modelspace()
    for item in items:
        text = parsed.get(item["number"]) or item.get("comment") or f"п. {item['number']}"
        item["text"] = text
        shelf_x, shelf_y = _shelf(doc, (item["x"], item["y"]), text)
        msp.add_line((item["x"], item["y"]), (shelf_x, shelf_y), dxfattribs={"layer": LAYER})
        msp.add_line((shelf_x, shelf_y), (shelf_x + max(8.0, len(text) * 2.0), shelf_y), dxfattribs={"layer": LAYER})
        msp.add_text(f"{item['number']} {text}", dxfattribs={"insert": (shelf_x, shelf_y + 1), "height": 2.5, "layer": LAYER})
    written = False
    if dest is not None:
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        doc.saveas(str(dest))
        written = True
    return {
        "check_only": False,
        "written": written,
        "leaders": items,
        "note": "Полка сдвигается проверкой свободного места. Номер пункта ГОСТ для длины полки не сверен.",
        "original_unchanged": hashlib.sha256(src.read_bytes()).hexdigest() == before,
    }


def leaders_cli(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="gost-check leaders")
    parser.add_argument("input")
    parser.add_argument("--out")
    parser.add_argument("--notes", default="")
    args = parser.parse_args(argv)
    result = write_leaders(Path(args.input), Path(args.out) if args.out else None, args.notes)
    print(json.dumps(result, ensure_ascii=False))
    return 0
