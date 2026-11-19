"""Контуры помещений из стен. Площадь по внутреннему контуру — не норма, а геометрическая оценка."""

from __future__ import annotations

from pathlib import Path

NOTE = "Площадь по внутреннему контуру, уточните по нормативу."


def plan_from_walls(walls: list[tuple[tuple[float, float], tuple[float, float]]], gap: float = 200.0) -> list:
    from shapely.geometry import LineString
    from shapely.ops import polygonize, unary_union

    lines = [LineString([start, end]) for start, end in walls if start != end]
    if not lines:
        return []
    merged = unary_union(lines)
    # замыкаем короткие разрывы проёмов
    closed = merged.buffer(gap / 2.0).buffer(-gap / 2.0)
    polygons = [poly for poly in polygonize(closed.boundary) if poly.area > 1.0]
    polygons.sort(key=lambda poly: (-poly.centroid.y, poly.centroid.x))
    return polygons


def roomplan_cli(argv: list[str]) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(prog="gost-check roomplan")
    parser.add_argument("input")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(write_plan(Path(args.input), Path(args.out)), ensure_ascii=False))
    return 0


def write_plan(src: Path, dest: Path, texts: list[dict] | None = None) -> dict:
    import ezdxf

    original = Path(src).read_bytes()
    doc = ezdxf.readfile(str(src))
    walls = []
    for entity in doc.modelspace():
        layer = str(entity.dxf.layer or "").casefold()
        if entity.dxftype() == "LINE" and any(token in layer for token in ("стен", "wall", "перег")):
            walls.append(((entity.dxf.start.x, entity.dxf.start.y), (entity.dxf.end.x, entity.dxf.end.y)))
    polygons = plan_from_walls(walls)
    if "АВТО_ПОМЕЩЕНИЯ" not in doc.layers:
        doc.layers.add("АВТО_ПОМЕЩЕНИЯ")
    msp = doc.modelspace()
    rooms = []
    for index, poly in enumerate(polygons, start=1):
        coords = [(float(x), float(y)) for x, y in poly.exterior.coords]
        msp.add_lwpolyline(coords, close=True, dxfattribs={"layer": "АВТО_ПОМЕЩЕНИЯ"})
        from shapely.geometry import Point

        center = poly.centroid
        name = "без названия"
        number = str(index)
        for text in texts or []:
            if poly.contains(Point(text["x"], text["y"])):
                if text.get("name"):
                    name = text["name"]
                if text.get("number"):
                    number = str(text["number"])
        msp.add_text(number, dxfattribs={"insert": (center.x, center.y), "height": 250, "layer": "АВТО_ПОМЕЩЕНИЯ"})
        rooms.append({"number": number, "name": name, "area_m2": round(poly.area / 1_000_000.0, 2)})
    dest.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(str(dest))
    return {
        "rooms": rooms,
        "total": round(sum(item["area_m2"] for item in rooms), 2),
        "note": NOTE,
        "draft": "черновик",
        "original_unchanged": Path(src).read_bytes() == original,
    }
