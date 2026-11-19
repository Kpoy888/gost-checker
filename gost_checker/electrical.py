"""Минимальные расстояния от электроустановочных изделий. Пороги задаёт пользователь.

Пресет пустой: числа ПУЭ и СП здесь не приведены, их нужно сверить самостоятельно.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from .models import CheckResult
from .sheetspace import open_doc, to_mm

DEFAULT_FIXTURES = {
    "розетка": [r"розет", r"socket", r"outlet"],
    "выключатель": [r"выключ", r"switch"],
    "коробка": [r"короб", r"junction"],
    "щит": [r"щит", r"panel"],
}
DEFAULT_TARGETS = {
    "труба": [r"(^|[^а-яa-z])(в1|т3|к1)([^а-яa-z]|$)", r"отоп", r"газ", r"pipe"],
    "окно": [r"окн", r"window"],
    "дверь": [r"двер", r"door"],
    "санприбор": [r"раков", r"ванн", r"душ", r"sink"],
    "газовое": [r"газов", r"gas"],
    "радиатор": [r"радиат", r"radiator"],
}


def load_rules(payload) -> dict:
    if payload is None:
        return {"rules": [], "note": "Пороги не заданы. Сверьте расстояния с действующими ПУЭ и СП самостоятельно."}
    if isinstance(payload, (str, Path)):
        text = Path(payload).read_text(encoding="utf-8")
        if str(payload).endswith((".yaml", ".yml")):
            try:
                import yaml
            except ImportError as exc:
                raise ValueError("Для YAML нужен PyYAML") from exc
            payload = yaml.safe_load(text)
        else:
            payload = json.loads(text)
    if not isinstance(payload, dict) or not isinstance(payload.get("rules", []), list):
        raise ValueError("Правила должны быть объектом с полем rules")
    for rule in payload.get("rules") or []:
        if not isinstance(rule, dict):
            raise ValueError("Правило должно быть объектом")
        if "fixture" not in rule or "target" not in rule or "min_mm" not in rule:
            raise ValueError("В правиле нужны fixture, target и min_mm")
        if float(rule["min_mm"]) < 0:
            raise ValueError("min_mm не может быть отрицательным")
    payload.setdefault("note", "Пороги заданы пользователем, это не цитата ПУЭ.")
    return payload


def _kind(name: str, table: dict) -> Optional[str]:
    hay = (name or "").casefold()
    for kind, patterns in table.items():
        for pattern in patterns:
            if re.search(pattern, hay, re.IGNORECASE):
                return kind
    return None


def _insert_box(entity):
    try:
        from ezdxf import bbox as ezbbox

        ext = ezbbox.extents([entity], fast=True)
        if ext is not None and getattr(ext, "has_data", True):
            return (float(ext.extmin.x), float(ext.extmin.y), float(ext.extmax.x), float(ext.extmax.y))
    except Exception:
        pass
    ins = entity.dxf.insert
    return (float(ins.x), float(ins.y), float(ins.x), float(ins.y))


def _height_attr(entity) -> Optional[float]:
    try:
        for attrib in entity.attribs:
            tag = str(attrib.dxf.tag or "").casefold()
            if tag in {"отм", "h", "height", "высота"}:
                return float(str(attrib.dxf.text).replace(",", "."))
    except Exception:
        return None
    return None


def analyze_doc(doc, rules: Optional[dict] = None) -> dict:
    rules = load_rules(rules)
    unit = to_mm(doc)
    fixtures = []
    unknown = []
    targets = []
    for entity in doc.modelspace():
        if entity.dxftype() != "INSERT":
            layer = str(entity.dxf.layer or "")
            kind = _kind(layer, DEFAULT_TARGETS)
            if kind and entity.dxftype() == "LINE":
                targets.append({
                    "kind": kind,
                    "layer": layer,
                    "p1": (float(entity.dxf.start.x), float(entity.dxf.start.y)),
                    "p2": (float(entity.dxf.end.x), float(entity.dxf.end.y)),
                })
            continue
        label = f"{entity.dxf.name} {entity.dxf.layer}"
        fixture = _kind(label, DEFAULT_FIXTURES)
        target = _kind(label, DEFAULT_TARGETS)
        box = _insert_box(entity)
        if fixture:
            fixtures.append({"kind": fixture, "name": str(entity.dxf.name), "layer": str(entity.dxf.layer), "box": box, "height": _height_attr(entity), "handle": str(entity.dxf.handle)})
        elif target:
            targets.append({"kind": target, "layer": str(entity.dxf.layer), "p1": (box[0], box[1]), "p2": (box[2], box[3]), "insert": True})
        else:
            unknown.append({"name": str(entity.dxf.name), "layer": str(entity.dxf.layer), "handle": str(entity.dxf.handle)})
    violations = []
    notes = []
    from shapely.geometry import LineString, Point, box as shapely_box

    for fixture in fixtures:
        if fixture["height"] is not None:
            notes.append(f"{fixture['name']}: есть отметка монтажа, но у элементов чертежа нет высоты — проверена только плановая дистанция.")
        shape = shapely_box(*fixture["box"]) if fixture["box"][2] != fixture["box"][0] or fixture["box"][3] != fixture["box"][1] else Point(fixture["box"][0], fixture["box"][1])
        for rule in rules.get("rules") or []:
            if rule["fixture"] != fixture["kind"]:
                continue
            best = None
            for target in targets:
                if target["kind"] != rule["target"]:
                    continue
                line = LineString([target["p1"], target["p2"]])
                dist_mm = float(shape.distance(line)) * unit
                if best is None or dist_mm < best[0]:
                    best = (dist_mm, target)
            if best is None:
                continue
            dist_mm, target = best
            minimum = float(rule["min_mm"])
            if dist_mm + 1e-6 < minimum:
                cx = (fixture["box"][0] + fixture["box"][2]) / 2
                cy = (fixture["box"][1] + fixture["box"][3]) / 2
                violations.append({
                    "fixture": fixture["kind"],
                    "name": fixture["name"],
                    "target": target["kind"],
                    "actual_mm": round(dist_mm, 2),
                    "required_mm": minimum,
                    "handle": fixture["handle"],
                    "bbox": [cx - 1, cy - 1, cx + 1, cy + 1],
                    "message": f"{fixture['kind']} ближе к «{target['kind']}»: {dist_mm:.1f} мм при минимуме {minimum:.1f} мм.",
                })
    return {"violations": violations, "unknown": unknown, "notes": notes, "rules_note": rules.get("note", "")}


def gaps_cli(argv: list[str]) -> int:
    import argparse
    import json as jsonlib

    parser = argparse.ArgumentParser(prog="gost-check gaps")
    parser.add_argument("input")
    parser.add_argument("--rules", required=True)
    args = parser.parse_args(argv)
    try:
        rules = load_rules(args.rules)
    except ValueError as exc:
        print(str(exc))
        return 2
    result = check_file(Path(args.input), rules)
    print(jsonlib.dumps({"message": result.message, "details": result.details}, ensure_ascii=False, indent=2))
    return 0 if result.severity in {"ok", "info"} else 2


def check_file(path: Path, rules: Optional[dict] = None) -> CheckResult:
    data = analyze_doc(open_doc(path), rules)
    details = list(data["notes"])
    details.append(data["rules_note"])
    if data["unknown"]:
        details.append("Не распознаны блоки: " + ", ".join(item["name"] for item in data["unknown"][:12]))
    if not data["violations"]:
        return CheckResult("electrical_gap", "Расстояния электроустановочных изделий", "правила пользователя", "ok" if rules and rules.get("rules") else "info", "Нарушений заданных минимальных расстояний нет." if rules and rules.get("rules") else "Пороги расстояний не заданы, числа ПУЭ и СП программа не подставляет.", details=details, extra={"unknown": data["unknown"]})
    return CheckResult(
        "electrical_gap",
        "Расстояния электроустановочных изделий",
        "правила пользователя",
        "warning",
        f"Нарушений минимальных расстояний: {len(data['violations'])}.",
        details=details + [item["message"] for item in data["violations"][:20]],
        findings_count=len(data["violations"]),
        extra={"markers": [{"text": item["message"], "bbox": item["bbox"], "space": "model"} for item in data["violations"]], "violations": data["violations"], "unknown": data["unknown"]},
    )
