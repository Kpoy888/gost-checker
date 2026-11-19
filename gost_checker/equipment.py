"""Марки оборудования по известным именам блоков.

Неизвестный блок попадает в список и не получает выдуманное имя.
Таблица простая: форма спецификации ГОСТ 21.110 не сверена.
Подсчёт геометрии берётся из ведомости объёмов, без второй реализации.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from .quantities import takeoff_dxf

SPEC_NOTE = "Простая таблица. Форма спецификации ГОСТ 21.110 не сверена."
CATALOG_NOTE = "Сопоставление имён блоков — справочник пользователя. Это не каталог типовых узлов."

DEFAULT_CATALOG = {
    "радиатор": {"mark": "Р", "name": "Радиатор"},
    "светильник": {"mark": "СВ", "name": "Светильник"},
    "розетк": {"mark": "РЗ", "name": "Розетка"},
    "щит": {"mark": "Щ", "name": "Щит"},
}


def load_catalog(source) -> dict:
    if source is None:
        return dict(DEFAULT_CATALOG)
    if isinstance(source, dict):
        return dict(source)
    text = Path(source).read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Справочник марок должен быть объектом")
    return data


def _lookup(block_name: str, catalog: dict) -> dict | None:
    folded = block_name.casefold()
    best = None
    best_len = -1
    for key, spec in catalog.items():
        token = str(key).casefold()
        if token and token in folded and len(token) > best_len:
            best = spec
            best_len = len(token)
    return best


def collect_inserts(doc, catalog: dict) -> tuple[list[dict], list[dict]]:
    known: dict[str, dict] = {}
    unknown: dict[str, dict] = {}
    for entity in doc.modelspace().query("INSERT"):
        name = str(entity.dxf.name)
        spec = _lookup(name, catalog)
        insert = entity.dxf.insert
        point = (float(insert.x), float(insert.y))
        if spec:
            row = known.get(name)
            if row is None:
                row = {
                    "block": name,
                    "mark": str(spec.get("mark") or ""),
                    "name": str(spec.get("name") or ""),
                    "count": 0,
                    "points": [],
                }
                known[name] = row
            row["count"] += 1
            row["points"].append(point)
        else:
            row = unknown.get(name)
            if row is None:
                row = {"block": name, "mark": "", "name": "", "count": 0, "note": "не распознано, имя не назначено"}
                unknown[name] = row
            row["count"] += 1
    return list(known.values()), list(unknown.values())


def write_equipment(src: Path, dest: Path, catalog=None) -> dict:
    import ezdxf

    src = Path(src)
    before = hashlib.sha256(src.read_bytes()).hexdigest()
    catalog = load_catalog(catalog)
    doc = ezdxf.readfile(str(src))
    known, unknown = collect_inserts(doc, catalog)
    geometry = takeoff_dxf(src)
    out = ezdxf.readfile(str(src))
    if "АВТО_МАРКИ" not in out.layers:
        out.layers.add("АВТО_МАРКИ")
    msp = out.modelspace()
    position = 1
    rows = []
    for item in known:
        rows.append(
            {
                "pos": position,
                "mark": item["mark"],
                "name": item["name"],
                "count": item["count"],
                "note": "подсчёт вхождений блока",
            }
        )
        for point in item["points"]:
            msp.add_text(
                str(position),
                dxfattribs={"insert": (point[0] + 50, point[1] + 50), "height": 250, "layer": "АВТО_МАРКИ"},
            )
        position += 1
    for item in unknown:
        rows.append({"pos": "", "mark": "", "name": "", "count": item["count"], "block": item["block"], "note": item["note"]})
    y = -500.0
    msp.add_text(SPEC_NOTE, dxfattribs={"insert": (0, y), "height": 200, "layer": "АВТО_МАРКИ"})
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.saveas(str(dest))
    csv_path = dest.with_suffix(".csv")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Поз.", "Обозначение", "Наименование", "Кол.", "Примечание"])
        for row in rows:
            writer.writerow([row.get("pos", ""), row.get("mark", ""), row.get("name", "") or row.get("block", ""), row["count"], row["note"]])
        writer.writerow(["", "", "", "", SPEC_NOTE])
    return {
        "rows": rows,
        "unknown": unknown,
        "known": [{k: v for k, v in item.items() if k != "points"} for item in known],
        "geometry_rows": geometry.get("rows") or [],
        "note": SPEC_NOTE,
        "catalog_note": CATALOG_NOTE,
        "dxf": str(dest),
        "csv": str(csv_path),
        "original_unchanged": hashlib.sha256(src.read_bytes()).hexdigest() == before,
    }


def equipment_cli(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="gost-check equipment")
    parser.add_argument("input")
    parser.add_argument("--out", required=True)
    parser.add_argument("--catalog")
    args = parser.parse_args(argv)
    result = write_equipment(Path(args.input), Path(args.out), args.catalog)
    print(json.dumps({k: v for k, v in result.items() if k != "geometry_rows"}, ensure_ascii=False))
    return 0
