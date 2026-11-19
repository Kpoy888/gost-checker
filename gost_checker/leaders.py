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
    """Сверху вниз и слева направо, без дыр. Прежний номер метки остаётся только для текста примечания."""
    ranked = sorted(points, key=lambda item: (-round(float(item["y"]), 3), round(float(item["x"]), 3)))
    assigned = []
    for index, item in enumerate(ranked, start=1):
        copy = dict(item)
        copy["source_number"] = item.get("number")
        copy["number"] = index
        assigned.append(copy)
    return assigned


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
            item["text"] = parsed.get(item.get("source_number")) or item.get("comment") or ""
        written = False
        if dest is not None:
            _write_leader_dxf(dest, items)
            written = True
        return {
            "check_only": True,
            "written": written,
            "leaders": items,
            "note": "PDF не изменяется. Выноски записаны в отдельный DXF.",
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
        text = parsed.get(item.get("source_number")) or item.get("comment") or f"п. {item['number']}"
        item["text"] = text
        shelf_x, shelf_y = _shelf(doc, (item["x"], item["y"]), text)
        msp.add_line((item["x"], item["y"]), (shelf_x, shelf_y), dxfattribs={"layer": LAYER})
        msp.add_line((shelf_x, shelf_y), (shelf_x + max(8.0, len(text) * 2.0), shelf_y), dxfattribs={"layer": LAYER})
        msp.add_text(f"{item['number']} {text}", dxfattribs={"insert": (shelf_x, shelf_y + 1), "height": 2.5, "layer": LAYER})
    if items:
        listing = "\\P".join(f"{item['number']} {item['text']}" for item in items)
        msp.add_mtext(
            "Примечания\\P" + listing,
            dxfattribs={"insert": (20, 70), "char_height": 2.5, "layer": LAYER, "width": 160},
        )
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


def _write_leader_dxf(dest: Path, items: list[dict]) -> None:
    import ezdxf

    doc = ezdxf.new("R2010")
    doc.layers.add(LAYER)
    msp = doc.modelspace()
    for item in items:
        text = item.get("text") or ""
        x, y = float(item["x"]), float(item["y"])
        msp.add_line((x, y), (x + 15, y + 12), dxfattribs={"layer": LAYER})
        msp.add_text(f"{item['number']} {text}".strip(), dxfattribs={"insert": (x + 15, y + 13), "height": 2.5, "layer": LAYER})
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(str(dest))


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
