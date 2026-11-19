"""Ведомость материалов поверх ведомости объёмов.

Объёмы не считаются заново: берутся строки takeoff_ifc / takeoff_dxf.
Число кирпичей — объём модели, делённый на объём одного кирпича 250×120×65 мм.
Норма расхода с раствором не применяется. Неизвестный материал остаётся в списке.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .quantities import takeoff_path

BRICK_M = (0.250, 0.120, 0.065)
BRICK_VOLUME_M3 = BRICK_M[0] * BRICK_M[1] * BRICK_M[2]
NOTE = (
    "Число кирпичей = объём из ведомости / объём одного кирпича 250×120×65 мм. "
    "Норма расхода раствора не применялась. Нераспознанный материал не переименовывается."
)

DEFAULT_KEYWORDS = {
    "кирпич": ["кирпич", "brick", "masonry"],
    "штукатурка": ["штукатур", "plaster", "gypsum"],
    "бетон": ["бетон", "concrete"],
}


def load_keywords(source) -> dict:
    if source is None:
        return {key: list(value) for key, value in DEFAULT_KEYWORDS.items()}
    if isinstance(source, dict):
        return {str(key): list(value) for key, value in source.items()}
    text = Path(source).read_text(encoding="utf-8")
    if text.lstrip().startswith("{"):
        data = json.loads(text)
        return {str(key): list(value) for key, value in data.items()}
    data: dict[str, list[str]] = {}
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith(":") and not line.startswith("-"):
            current = line[:-1].strip()
            data[current] = []
            continue
        if current and line.startswith("-"):
            data[current].append(line[1:].strip())
    return data


def classify(name: str, keywords: dict) -> str:
    folded = (name or "").casefold()
    if not folded:
        return ""
    for label, tokens in keywords.items():
        if any(token.casefold() in folded for token in tokens):
            return label
    return ""


def _brick_row(row: dict) -> dict:
    volume = row.get("volume_m3")
    item = {
        "material": "кирпич",
        "name": row.get("name") or "",
        "storey": row.get("storey") or "",
        "unit": "шт",
        "quantity": None,
        "source": "недостаточно данных",
    }
    if volume:
        item["quantity"] = round(float(volume) / BRICK_VOLUME_M3, 2)
        item["source"] = "объём модели / объём одного кирпича, без нормы расхода"
    return item


def _plaster_from_row(row: dict) -> dict:
    area = row.get("area_m2")
    return {
        "material": "штукатурка",
        "name": row.get("name") or "",
        "storey": row.get("storey") or "",
        "unit": "м²",
        "quantity": round(float(area), 3) if area else None,
        "source": "из модели" if area else "недостаточно данных",
    }


def _openings(rows: list[dict], thickness_m: float | None) -> list[dict]:
    found = []
    for row in rows:
        if row.get("class") not in {"IfcDoor", "IfcWindow"} and "проём" not in str(row.get("name") or "").casefold():
            continue
        area = row.get("area_m2")
        item = {
            "material": "штукатурка",
            "name": row.get("name") or row.get("class") or "проём",
            "storey": row.get("storey") or "",
            "unit": "м²",
            "quantity": round(float(area), 3) if area else None,
            "source": "проём × заданная толщина" if area and thickness_m else "недостаточно данных",
            "thickness_m": thickness_m,
        }
        if area and thickness_m:
            item["volume_m3"] = round(float(area) * float(thickness_m), 4)
        found.append(item)
    return found


def materials_from_takeoff(data: dict, keywords: dict, plaster_thickness_m: float | None = None) -> dict:
    rows = []
    unrecognized = []
    classified_plaster = False
    for row in data.get("rows") or []:
        label = classify(str(row.get("material") or row.get("name") or ""), keywords)
        if not label:
            title = str(row.get("material") or row.get("name") or "").strip()
            if title:
                unrecognized.append({"name": title, "element": row.get("name") or "", "storey": row.get("storey") or ""})
            continue
        if label == "кирпич":
            rows.append(_brick_row(row))
        elif label == "штукатурка":
            classified_plaster = True
            rows.append(_plaster_from_row(row))
        else:
            unit = "м³" if row.get("volume_m3") else "м²" if row.get("area_m2") else "шт"
            quantity = row.get("volume_m3") if unit == "м³" else row.get("area_m2") if unit == "м²" else row.get("count")
            rows.append(
                {
                    "material": label,
                    "name": row.get("name") or "",
                    "storey": row.get("storey") or "",
                    "unit": unit,
                    "quantity": quantity,
                    "source": row.get("source") or "",
                }
            )
    openings = [] if classified_plaster else _openings(data.get("rows") or [], plaster_thickness_m)
    return {"rows": rows + openings, "unrecognized": unrecognized, "note": NOTE}


def write_materials(src: Path, out_dir: Path, keywords=None, plaster_thickness_m: float | None = None) -> dict:
    data = takeoff_path(Path(src))
    parsed = load_keywords(keywords)
    table = materials_from_takeoff(data, parsed, plaster_thickness_m) if data.get("ok") else {"rows": [], "unrecognized": [], "note": NOTE}
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "materials.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Материал", "Элемент", "Этаж", "Ед.", "Количество", "Источник"])
        for row in table["rows"]:
            writer.writerow([row["material"], row["name"], row["storey"], row["unit"], row["quantity"] if row["quantity"] is not None else "", row["source"]])
        for row in table["unrecognized"]:
            writer.writerow([row["name"], row["element"], row["storey"], "", "", "не распознан"])
        writer.writerow(["", "", "", "", "", NOTE])
    return {
        "ok": bool(data.get("ok")),
        "error": data.get("error") or "",
        "rows": table["rows"],
        "unrecognized": table["unrecognized"],
        "note": NOTE,
        "csv": str(csv_path),
    }


def materials_cli(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="gost-check materials")
    parser.add_argument("input")
    parser.add_argument("--out", required=True)
    parser.add_argument("--keywords")
    parser.add_argument("--plaster-thickness", type=float, default=None)
    args = parser.parse_args(argv)
    result = write_materials(Path(args.input), Path(args.out), args.keywords, args.plaster_thickness)
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, ensure_ascii=False))
    print(json.dumps(result["rows"], ensure_ascii=False))
    return 0 if result["ok"] else 2
