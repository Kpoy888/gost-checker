"""Экспликация помещений и подписи на чертеже.

Версии как в Git и совместная работа отменены, поэтому источник истины — JSON рядом с результатом.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Optional

APPID = "GOST_ROOM"
TAG_NUMBER = ("ROOM_NUMBER", "НОМЕР", "NOMER")
TAG_NAME = ("ROOM_NAME", "НАИМЕНОВАНИЕ", "NAME")
TAG_AREA = ("AREA", "ПЛОЩАДЬ", "PLOSH")
HEADER_MAP = {
    "номер": "number",
    "номер помещения": "number",
    "наименование": "name",
    "имя": "name",
    "площадь": "area",
    "категория": "category",
}


def parse_table_rows(rows: list[list[str]]) -> list[dict]:
    if not rows:
        return []
    header = [str(cell or "").strip().casefold() for cell in rows[0]]
    columns = []
    for cell in header:
        columns.append(HEADER_MAP.get(cell, cell))
    parsed = []
    for raw in rows[1:]:
        item = {columns[i]: str(raw[i]).strip() if i < len(raw) and raw[i] is not None else "" for i in range(len(columns))}
        if not item.get("number"):
            continue
        parsed.append({
            "number": item.get("number", ""),
            "name": item.get("name", ""),
            "area": item.get("area", ""),
            "category": item.get("category", ""),
        })
    return parsed


def read_acad_table(doc) -> list[dict]:
    try:
        from ezdxf.entities.acad_table import read_acad_table_content
    except Exception:
        return []
    for entity in doc.modelspace():
        if entity.dxftype() != "ACAD_TABLE":
            continue
        try:
            return parse_table_rows(read_acad_table_content(entity))
        except Exception:
            continue
    return []


def read_line_grid(doc) -> list[dict]:
    texts = []
    xs, ys = set(), set()
    for entity in doc.modelspace():
        if entity.dxftype() == "LINE":
            xs.add(round(float(entity.dxf.start.x), 3))
            xs.add(round(float(entity.dxf.end.x), 3))
            ys.add(round(float(entity.dxf.start.y), 3))
            ys.add(round(float(entity.dxf.end.y), 3))
        elif entity.dxftype() in {"TEXT", "MTEXT"}:
            ins = entity.dxf.insert
            content = entity.plain_text() if entity.dxftype() == "MTEXT" else str(entity.dxf.text or "")
            texts.append((float(ins.x), float(ins.y), content.strip()))
    if len(xs) < 2 or len(ys) < 2 or not texts:
        return []
    x_lines = sorted(xs)
    y_lines = sorted(ys)
    grid: dict[tuple[int, int], str] = {}
    for x, y, content in texts:
        col = max(i for i, edge in enumerate(x_lines[:-1]) if edge <= x)
        row = max(i for i, edge in enumerate(y_lines[:-1]) if edge <= y)
        grid[(row, col)] = content
    if not grid:
        return []
    max_row = max(key[0] for key in grid)
    max_col = max(key[1] for key in grid)
    rows = [[grid.get((row, col), "") for col in range(max_col + 1)] for row in range(max_row + 1)]
    # в DXF ось Y вверх, первая строка таблицы — верхняя
    rows.reverse()
    return parse_table_rows(rows)


def read_csv(path: Path) -> list[dict]:
    with Path(path).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    return parse_table_rows(rows)


def read_xlsx(path: Path) -> list[dict]:
    from openpyxl import load_workbook

    book = load_workbook(path, data_only=True)
    sheet = book.active
    rows = [[cell.value if cell.value is not None else "" for cell in row] for row in sheet.iter_rows()]
    return parse_table_rows(rows)


def read_ifc_spaces(path: Path) -> list[dict]:
    import ifcopenshell

    model = ifcopenshell.open(str(path))
    rows = []
    for space in model.by_type("IfcSpace"):
        number = str(getattr(space, "Name", "") or "")
        long_name = str(getattr(space, "LongName", "") or "")
        rows.append({"number": number, "name": long_name, "area": "", "category": ""})
    return rows


def _layouts(doc):
    seen = set()
    for layout in [doc.modelspace(), *list(doc.layouts)]:
        if id(layout) in seen:
            continue
        seen.add(id(layout))
        yield layout


def _attrib(insert, tags: tuple[str, ...]):
    try:
        for attrib in insert.attribs:
            if str(attrib.dxf.tag or "").upper() in tags:
                return attrib
    except Exception:
        return None
    return None


def _xdata_id(entity) -> str:
    try:
        tags = entity.get_xdata(APPID)
    except Exception:
        return ""
    for tag in tags or []:
        if getattr(tag, "code", None) == 1000:
            return str(tag.value)
    return ""


def _set_xdata(doc, entity, room_id: str) -> None:
    if APPID not in doc.appids:
        doc.appids.add(APPID)
    entity.set_xdata(APPID, [(1000, room_id)])


def collect_labels(doc) -> list[dict]:
    labels = []
    for layout in _layouts(doc):
        space = getattr(layout, "name", "Model")
        for entity in layout:
            if entity.dxftype() == "INSERT":
                number = _attrib(entity, TAG_NUMBER)
                name = _attrib(entity, TAG_NAME)
                if number is None and name is None:
                    continue
                labels.append({
                    "kind": "attrib",
                    "space": space,
                    "entity": entity,
                    "number_entity": number,
                    "name_entity": name,
                    "number": str(number.dxf.text or "") if number is not None else "",
                    "name": str(name.dxf.text or "") if name is not None else "",
                    "room_id": _xdata_id(entity),
                    "insert": (float(entity.dxf.insert.x), float(entity.dxf.insert.y)),
                    "height": float(number.dxf.height or 2.5) if number is not None else 2.5,
                })
            elif entity.dxftype() in {"TEXT", "MTEXT"}:
                content = entity.plain_text() if entity.dxftype() == "MTEXT" else str(entity.dxf.text or "")
                match = re.match(r"^\s*(\d+)\b", content.strip())
                if not match:
                    continue
                labels.append({
                    "kind": "text",
                    "space": space,
                    "entity": entity,
                    "number": match.group(1),
                    "name": content.strip(),
                    "room_id": _xdata_id(entity),
                    "insert": (float(entity.dxf.insert.x), float(entity.dxf.insert.y)),
                    "height": float(getattr(entity.dxf, "height", 2.5) or getattr(entity.dxf, "char_height", 2.5) or 2.5),
                })
    return labels


def diff_labels(doc, rows: list[dict]) -> list[dict]:
    by_number = {row["number"]: row for row in rows}
    by_id = {}
    labels = collect_labels(doc)
    changes = []
    seen = []
    duplicates = {}
    for label in labels:
        duplicates.setdefault(label["number"], 0)
        duplicates[label["number"]] += 1
        row = by_number.get(label["number"])
        if label["room_id"] and label["room_id"] in {item.get("id") for item in rows}:
            row = next(item for item in rows if item.get("id") == label["room_id"])
        if row is None:
            changes.append({"kind": "label_without_row", "number": label["number"], "space": label["space"]})
            continue
        seen.append(row["number"])
        desired = row.get("name") or ""
        current = label["name"]
        if label["kind"] == "text":
            current_name = re.sub(r"^\s*\d+\s*", "", current).strip()
        else:
            current_name = current
        if desired and current_name != desired:
            changes.append({
                "kind": "rename",
                "number": row["number"],
                "was": current_name,
                "new": desired,
                "space": label["space"],
                "label": label,
            })
        if duplicates[label["number"]] > 1:
            changes.append({"kind": "duplicate", "number": label["number"], "space": label["space"]})
    for row in rows:
        if row["number"] not in seen:
            changes.append({"kind": "missing_label", "number": row["number"], "name": row.get("name", "")})
    return changes


def apply_changes(doc, changes: list[dict], rows: list[dict]) -> list[str]:
    warnings = []
    by_number = {row["number"]: row for row in rows}
    for change in changes:
        if change["kind"] != "rename":
            continue
        label = change["label"]
        row = by_number[change["number"]]
        if label["kind"] == "attrib" and label.get("name_entity") is not None:
            attrib = label["name_entity"]
            height = float(attrib.dxf.height or label["height"])
            attrib.dxf.text = row.get("name") or ""
        else:
            entity = label["entity"]
            height = label["height"]
            entity.dxf.text = f"{row['number']} {row.get('name') or ''}".strip()
        room_id = row.get("id") or row["number"]
        _set_xdata(doc, label["entity"], room_id)
        if len(row.get("name") or "") * 0.6 * height > 40:
            warnings.append(f"Помещение {row['number']}: новое имя может не поместиться в контур.")
    for row in rows:
        for label in collect_labels(doc):
            if label["number"] == row["number"] and not label["room_id"]:
                _set_xdata(doc, label["entity"], row.get("id") or row["number"])
    return warnings


def sync_drawing(path: Path, rows: list[dict], dest: Path, apply: bool) -> dict:
    import ezdxf

    doc = ezdxf.readfile(str(path))
    changes = diff_labels(doc, rows)
    warnings = []
    if apply:
        warnings = apply_changes(doc, changes, rows)
        dest.parent.mkdir(parents=True, exist_ok=True)
        doc.saveas(str(dest))
        again = diff_labels(ezdxf.readfile(str(dest)), rows)
        renames_left = [item for item in again if item["kind"] == "rename"]
    else:
        renames_left = [item for item in changes if item["kind"] == "rename"]
        again = changes
    preview = [{key: value for key, value in item.items() if key != "label"} for item in changes]
    return {"changes": preview, "warnings": warnings, "remaining_renames": len(renames_left), "audit_errors": _audit(dest if apply else None)}


def _audit(path: Optional[Path]) -> int:
    if path is None or not Path(path).is_file():
        return 0
    import ezdxf

    doc = ezdxf.readfile(str(path))
    auditor = doc.audit()
    return len(list(auditor.errors)) if hasattr(auditor, "errors") else int(bool(auditor.has_errors))


def rooms_cli(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="gost-check rooms")
    parser.add_argument("input")
    parser.add_argument("--table", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    table = Path(args.table)
    rows = read_xlsx(table) if table.suffix.lower() == ".xlsx" else read_csv(table)
    result = sync_drawing(Path(args.input), rows, Path(args.out), apply=args.apply)
    print(json.dumps({"changes": len(result["changes"]), "warnings": result["warnings"]}, ensure_ascii=False))
    return 0


def save_source(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
