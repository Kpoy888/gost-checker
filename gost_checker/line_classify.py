"""Классификация линий по ГОСТ 2.303-68 и цветная схема наложения."""

from __future__ import annotations

import math
from collections import Counter
from pathlib import Path
from typing import Optional

from .constants import MAIN_LINE_S_MAX_MM, MAIN_LINE_S_MIN_MM
from .frame_geometry import entity_segments, iter_layout_entities

LINE_KIND_TITLES = {
    "solid_main": "Сплошная основная",
    "solid_thin": "Сплошная тонкая",
    "dashed": "Штриховая",
    "dashdot": "Штрихпунктирная (осевая)",
    "wavy": "Волнистая / с изломами",
    "frame": "Рамка листа",
    "titleblock": "Линии штампа",
    "unknown": "Не определена",
}
LINE_KIND_COLORS = {
    "solid_main": "#1a1a1a",
    "solid_thin": "#6a6a6a",
    "dashed": "#1d5aa6",
    "dashdot": "#b3261e",
    "wavy": "#1b7a3d",
    "frame": "#2b6cb0",
    "titleblock": "#6b46c1",
    "unknown": "#c47b16",
}
AXIS_NAMES = {"CENTER", "CENTER2", "CENTERX2", "DASHDOT", "DASHDOT2", "DASHDOTX2", "ACAD_ISO10W100", "ACAD_ISO11W100"}
DASH_NAMES = {"DASHED", "DASHED2", "DASHEDX2", "HIDDEN", "HIDDEN2", "HIDDENX2", "ACAD_ISO02W100", "ACAD_ISO03W100"}
WAVY_NAMES = {"ZIGZAG", "TRACKS", "GAS_LINE"}
DASH_LEN = (2.0, 8.0)
DASH_GAP = (1.0, 2.0)
DASHDOT_LEN = (5.0, 30.0)
DASHDOT_GAP = (3.0, 5.0)


def linetype_pattern_mm(doc, name: str, to_mm: float) -> list[float]:
    if not name:
        return []
    try:
        lt = doc.linetypes.get(name)
    except Exception:
        return []
    values: list[float] = []
    try:
        pattern = getattr(lt, "pattern", None)
        if pattern:
            values = [float(x) for x in pattern]
    except Exception:
        values = []
    if not values:
        try:
            for tag in getattr(lt, "pattern_tags", []) or []:
                if getattr(tag, "code", None) == 49:
                    values.append(float(tag.value))
        except Exception:
            pass
    return [v * to_mm for v in values]


def classify_name(name: str) -> Optional[str]:
    n = (name or "").upper()
    if n in WAVY_NAMES or "ZIG" in n or "WAVE" in n:
        return "wavy"
    if n in AXIS_NAMES or "CENTER" in n or "DASHDOT" in n:
        return "dashdot"
    if n in DASH_NAMES or n.startswith("DASH") or n.startswith("HIDDEN"):
        return "dashed"
    if n in {"CONTINUOUS", "BYLAYER", "BYBLOCK", ""}:
        return None
    return None


def classify_weight(thickness: Optional[float], s_main: float) -> str:
    if thickness is None:
        return "solid_thin"
    if thickness >= MAIN_LINE_S_MIN_MM * 0.9:
        return "solid_main"
    return "solid_thin"


def pattern_kind(pattern: list[float]) -> Optional[str]:
    if not pattern:
        return None
    positives = [abs(x) for x in pattern if x > 1e-6]
    zeros = sum(1 for x in pattern if abs(x) < 1e-9)
    negatives = [abs(x) for x in pattern if x < -1e-6]
    if zeros and positives and negatives:
        return "dashdot"
    if len(positives) >= 1 and negatives and not zeros:
        # короткий штрих как «точка» только вместе с явно длинным
        dots = [x for x in positives if x <= 2.5]
        longs = [x for x in positives if x >= 5.0]
        if dots and longs:
            return "dashdot"
        return "dashed"
    return None


def raster_pattern_kind(pattern: list[float], ink_ratio: float) -> Optional[str]:
    """Для растра: осевая только при реальном чередовании длинный штрих + точка + промежутки."""
    if ink_ratio >= 0.91:
        return None
    if not pattern:
        return None
    dashes = [x for x in pattern if x > 0.35]
    gaps = [abs(x) for x in pattern if x < -0.35]
    if len(dashes) < 3 or len(gaps) < 3:
        return None
    if max(gaps) < 0.55:
        return None
    # нерегулярные просветы — скорее шум на сплошной, чем штриховка
    gaps_sorted = sorted(gaps)
    med_gap = gaps_sorted[len(gaps_sorted) // 2]
    regular = sum(1 for item in gaps if 0.45 * med_gap <= item <= 2.4 * med_gap)
    if regular < max(2, int(0.6 * len(gaps))):
        return None
    dots = [x for x in dashes if x <= 2.5]
    longs = [x for x in dashes if x >= 4.5]
    mid = [x for x in dashes if 2.0 <= x <= 10.0]
    # одна короткая «точка» на конце отрезка — обрезка, а не осевая
    if len(dots) >= 2 and longs:
        return "dashdot"
    if mid and len(dots) < 2:
        median = sorted(mid)[len(mid) // 2]
        if 2.0 <= median <= 8.5:
            return "dashed"
    return None


def check_pattern(kind: str, pattern: list[float]) -> list[str]:
    notes = []
    if not pattern:
        return notes
    dashes = sorted(abs(x) for x in pattern if x > 1e-6)
    gaps = sorted(abs(x) for x in pattern if x < -1e-6)
    if kind == "dashed" and dashes and gaps:
        if not (DASH_LEN[0] <= dashes[-1] <= DASH_LEN[1]):
            notes.append(f"длина штриха {dashes[-1]:.1f} мм вне 2–8 мм")
        if not (DASH_GAP[0] <= gaps[0] <= DASH_GAP[1] * 1.5):
            notes.append(f"промежуток {gaps[0]:.1f} мм вне 1–2 мм")
    if kind == "dashdot" and dashes:
        long = dashes[-1]
        if not (DASHDOT_LEN[0] * 0.6 <= long <= DASHDOT_LEN[1] * 1.2):
            notes.append(f"длинный штрих оси {long:.1f} мм вне 5–30 мм")
        if gaps and not (DASHDOT_GAP[0] * 0.5 <= gaps[0] <= DASHDOT_GAP[1] * 1.5):
            notes.append(f"промежуток оси {gaps[0]:.1f} мм вне 3–5 мм")
    return notes


def infer_s_main(thicknesses: list[float]) -> float:
    mains = [t for t in thicknesses if t is not None and t >= MAIN_LINE_S_MIN_MM]
    if not mains:
        return 0.7
    mains.sort()
    return max(MAIN_LINE_S_MIN_MM, min(mains[len(mains) // 2], MAIN_LINE_S_MAX_MM))


def _entity_length_mm(entity, to_mm: float, matrix=None) -> float:
    total = 0.0
    for x1, y1, x2, y2 in entity_segments(entity, matrix):
        total += math.hypot(x2 - x1, y2 - y1) * to_mm
    if total > 0:
        return total
    try:
        if entity.dxftype() == "CIRCLE":
            return 2 * math.pi * float(entity.dxf.radius) * to_mm
        if entity.dxftype() == "ARC":
            sweep = abs(float(entity.dxf.end_angle) - float(entity.dxf.start_angle)) * math.pi / 180.0
            return sweep * float(entity.dxf.radius) * to_mm
    except Exception:
        return 0.0
    return 0.0


def classify_dxf(doc, to_mm: float) -> dict:
    from .checks.linetypes import _entity_linetype, _entity_lw_mm
    items = []
    thicknesses = []
    layouts = [("Model", doc.modelspace())]
    for layout in doc.layouts:
        if layout.name.lower() != "model":
            layouts.append((layout.name, layout))
    seen = set()
    for space, layout in layouts:
        if id(layout) in seen:
            continue
        seen.add(id(layout))
        for entity, matrix in iter_layout_entities(doc, layout):
            dtype = entity.dxftype()
            if dtype not in {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "SPLINE", "ELLIPSE"}:
                continue
            thick = _entity_lw_mm(entity, doc)
            if thick is not None:
                thicknesses.append(thick)
    s_main = infer_s_main(thicknesses)
    seen.clear()
    for space, layout in layouts:
        if id(layout) in seen:
            continue
        seen.add(id(layout))
        for entity, matrix in iter_layout_entities(doc, layout):
            dtype = entity.dxftype()
            if dtype not in {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "SPLINE", "ELLIPSE"}:
                continue
            ltype = _entity_linetype(entity, doc)
            thick = _entity_lw_mm(entity, doc)
            pattern = linetype_pattern_mm(doc, ltype, to_mm)
            kind = classify_name(ltype) or pattern_kind(pattern)
            inferred_from_weight = kind is None
            if kind is None:
                kind = classify_weight(thick, s_main)
            notes = check_pattern(kind, pattern)
            if thick is None and inferred_from_weight:
                notes.append("толщина не указана (принята как тонкая)")
            if kind in {"dashed", "dashdot", "wavy", "solid_thin"} and thick and thick > 0:
                lo, hi = s_main / 3.0, s_main / 2.0
                if not (lo * 0.65 <= thick <= hi * 1.4) and thick < MAIN_LINE_S_MIN_MM:
                    # тонкая чуть вне отношения — замечание, не ошибка типа
                    if thick > hi * 1.4:
                        notes.append(f"толщина {thick:.2f} мм при S={s_main:.2f} вне S/3…S/2")
            if kind == "solid_main" and thick and thick > MAIN_LINE_S_MAX_MM + 0.05:
                notes.append(f"основная, толще нормы: {thick:.2f} мм (обычный S 0,5–1,4 мм)")
            segs = [(x1 * to_mm, y1 * to_mm, x2 * to_mm, y2 * to_mm) for x1, y1, x2, y2 in entity_segments(entity, matrix)]
            if dtype == "CIRCLE":
                c = entity.dxf.center
                r = float(entity.dxf.radius)
                segs.append(("circle", c.x * to_mm, c.y * to_mm, r * to_mm))
            items.append(
                {
                    "kind": kind,
                    "thickness_mm": thick,
                    "length_mm": _entity_length_mm(entity, to_mm, matrix),
                    "linetype": ltype,
                    "notes": notes,
                    "ok": not notes,
                    "segments": segs,
                    "space": space,
                    "dxftype": dtype,
                }
            )
    summary = summarize_items(items, s_main)
    summary["items"] = items
    return summary


def summarize_items(items: list[dict], s_main: float) -> dict:
    counter: Counter = Counter()
    length_by: Counter = Counter()
    issues = []
    for item in items:
        kind = item.get("kind") or "unknown"
        counter[kind] += 1
        length_by[kind] += float(item.get("length_mm") or 0)
        for note in item.get("notes") or []:
            issues.append(note)
    kinds = []
    for kind, title in LINE_KIND_TITLES.items():
        if counter[kind]:
            kinds.append(
                {
                    "kind": kind,
                    "title": title,
                    "count": counter[kind],
                    "length_mm": round(length_by[kind], 1),
                    "color": LINE_KIND_COLORS[kind],
                }
            )
    return {
        "s_main_mm": round(s_main, 3),
        "total": len(items),
        "kinds": kinds,
        "issues": issues[:30],
        "issue_count": len(issues),
    }


def classify_pdf_drawings(page, page_w_mm: float, page_h_mm: float) -> dict:
    pt = 25.4 / 72.0
    items = []
    thicknesses = []
    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []
    for drawing in drawings:
        width_pt = float(drawing.get("width") or 0)
        thick = width_pt * pt if width_pt else None
        if thick:
            thicknesses.append(thick)
    s_main = infer_s_main(thicknesses)
    for drawing in drawings:
        width_pt = float(drawing.get("width") or 0)
        thick = width_pt * pt if width_pt else None
        dashes = drawing.get("dashes")
        dash_values = []
        if dashes:
            seq = dashes[0] if isinstance(dashes, (list, tuple)) and dashes and isinstance(dashes[0], (list, tuple)) else dashes
            try:
                dash_values = [float(x) * pt for x in seq if isinstance(x, (int, float))]
            except Exception:
                dash_values = []
        pattern = []
        for i, value in enumerate(dash_values):
            pattern.append(value if i % 2 == 0 else -value)
        kind = pattern_kind(pattern) if pattern else classify_weight(thick, s_main)
        notes = check_pattern(kind, pattern) if pattern else []
        segs = []
        for item in drawing.get("items") or []:
            if not item:
                continue
            if item[0] == "l" and len(item) >= 3:
                p1, p2 = item[1], item[2]
                # PDF Y down -> flip for overlay consistency with page mm Y-up
                y1 = page_h_mm - float(p1.y) * pt
                y2 = page_h_mm - float(p2.y) * pt
                segs.append((float(p1.x) * pt, y1, float(p2.x) * pt, y2))
            elif item[0] == "re" and len(item) >= 2:
                rect = item[1]
                x0, y0 = float(rect.x0) * pt, page_h_mm - float(rect.y1) * pt
                x1, y1 = float(rect.x1) * pt, page_h_mm - float(rect.y0) * pt
                segs.extend(
                    [
                        (x0, y0, x1, y0),
                        (x1, y0, x1, y1),
                        (x1, y1, x0, y1),
                        (x0, y1, x0, y0),
                    ]
                )
        length = sum(math.hypot(a - c, b - d) for a, b, c, d in segs if isinstance(a, (int, float)))
        items.append(
            {
                "kind": kind or "unknown",
                "thickness_mm": thick,
                "length_mm": length,
                "linetype": "pdf-dash" if pattern else "pdf-solid",
                "notes": notes,
                "ok": not notes,
                "segments": segs,
                "space": "PDF",
                "dxftype": "PATH",
            }
        )
    summary = summarize_items(items, s_main)
    summary["items"] = items
    return summary


def _hex_rgb(color: str) -> tuple[int, int, int]:
    raw = (color or "#c47b16").lstrip("#")
    if len(raw) != 6:
        raw = "c47b16"
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)


def _overlay_font(size: int = 14):
    from PIL import ImageFont

    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def write_overlay_png(summary: dict, dest: Path, background=None, page_size_mm: Optional[tuple[float, float]] = None) -> Optional[str]:
    """Цветная схема линий через Pillow (без matplotlib — меньше памяти на 512 МБ)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        import numpy as np
        from PIL import Image, ImageDraw

        from .runtime_limits import overlay_max_side
    except Exception:
        return None

    items = list(summary.get("items") or [])
    max_side = overlay_max_side()
    raster_mode = False
    origin_x = 0.0
    origin_y = 0.0
    span_w = 1.0
    span_h = 1.0
    coord_scale = 1.0
    bg_img = None

    if background is not None:
        try:
            bg = np.asarray(background)
            if bg.ndim == 2:
                rgb = np.stack((bg, bg, bg), axis=-1)
            else:
                rgb = np.ascontiguousarray(bg[:, :, :3][:, :, ::-1])
            bh, bw = rgb.shape[:2]
            if max(bh, bw) > max_side:
                coord_scale = max_side / max(bh, bw)
                new_size = (max(1, int(bw * coord_scale)), max(1, int(bh * coord_scale)))
                bg_img = Image.fromarray(rgb).resize(new_size, Image.Resampling.BILINEAR)
            else:
                bg_img = Image.fromarray(rgb)
            raster_mode = True
        except Exception:
            bg_img = None
            raster_mode = False
            coord_scale = 1.0

    if raster_mode and bg_img is not None:
        img = bg_img.convert("RGB")
        width, height = img.size
    else:
        xs: list[float] = []
        ys: list[float] = []
        for item in items:
            for seg in item.get("segments") or []:
                if not seg:
                    continue
                if isinstance(seg[0], str) and seg[0] == "circle":
                    _, cx, cy, r = seg
                    xs.extend((cx - r, cx + r))
                    ys.extend((cy - r, cy + r))
                else:
                    try:
                        x1, y1, x2, y2 = seg[:4]
                    except Exception:
                        continue
                    xs.extend((x1, x2))
                    ys.extend((y1, y2))
        if page_size_mm:
            origin_x, origin_y = -5.0, -5.0
            span_w = float(page_size_mm[0]) + 10.0
            span_h = float(page_size_mm[1]) + 10.0
        elif xs and ys:
            origin_x = min(xs) - 8.0
            origin_y = min(ys) - 8.0
            span_w = max(xs) - min(xs) + 16.0
            span_h = max(ys) - min(ys) + 16.0
        else:
            origin_x, origin_y = 0.0, 0.0
            span_w, span_h = 420.0, 297.0
        span_w = max(span_w, 1.0)
        span_h = max(span_h, 1.0)
        fit = min(max_side / span_w, max_side / span_h)
        width = max(200, int(span_w * fit))
        height = max(200, int(span_h * fit))
        img = Image.new("RGB", (width, height), "white")

    draw = ImageDraw.Draw(img)

    def project(x: float, y: float) -> tuple[float, float]:
        if raster_mode:
            return x * coord_scale, y * coord_scale
        px = (x - origin_x) * (width / span_w)
        py = (span_h - (y - origin_y)) * (height / span_h)
        return px, py

    for item in items:
        color = _hex_rgb(LINE_KIND_COLORS.get(item.get("kind"), "#c47b16"))
        lw = 3 if item.get("kind") == "solid_main" else 2
        for seg in item.get("segments") or []:
            if not seg:
                continue
            if isinstance(seg[0], str) and seg[0] == "circle":
                if raster_mode:
                    continue
                _, cx, cy, r = seg
                x1, y1 = project(cx - r, cy - r)
                x2, y2 = project(cx + r, cy + r)
                box = [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]
                draw.ellipse(box, outline=color, width=lw)
                continue
            try:
                x1, y1, x2, y2 = seg[:4]
            except Exception:
                continue
            p1 = project(float(x1), float(y1))
            p2 = project(float(x2), float(y2))
            draw.line([p1, p2], fill=color, width=lw)

    font = _overlay_font(14)
    font_sm = _overlay_font(12)
    title = "Линии по типу (ГОСТ 2.303): основная, тонкая, штриховая, осевая"
    title_w = min(img.size[0] - 8, 16 + 8 * len(title))
    draw.rectangle((4, 4, title_w, 26), fill=(255, 255, 255))
    draw.text((8, 6), title, fill=(30, 30, 30), font=font)

    kinds = summary.get("kinds") or []
    if kinds:
        legend_w = 260
        row_h = 18
        legend_h = 10 + row_h * len(kinds)
        lx = max(8, img.size[0] - legend_w - 8)
        ly = 32
        draw.rectangle((lx, ly, lx + legend_w, ly + legend_h), fill=(255, 255, 255), outline=(180, 180, 180))
        yy = ly + 6
        for row in kinds:
            kind = row.get("kind")
            color = _hex_rgb(LINE_KIND_COLORS.get(kind, "#c47b16"))
            draw.rectangle((lx + 8, yy + 4, lx + 28, yy + 14), fill=color)
            label = LINE_KIND_TITLES.get(kind, kind or "?")
            draw.text((lx + 34, yy), str(label), fill=(30, 30, 30), font=font_sm)
            yy += row_h

    img.save(dest, format="PNG", optimize=True)
    return str(dest)


def details_from_summary(summary: dict) -> list[str]:
    details = [
        f"Принятая толщина основной линии S ≈ {summary.get('s_main_mm', 0):.2f} мм "
        f"(диапазон ГОСТ 2.303: {MAIN_LINE_S_MIN_MM:.1f}…{MAIN_LINE_S_MAX_MM:.1f} мм).",
        "Тонкая линия должна быть около S/3…S/2; штриховая — штрихи 2–8 мм и промежутки 1–2 мм; "
        "штрихпунктирная (осевая) — штрихи 5–30 мм и промежутки 3–5 мм.",
        f"Распознано графических объектов: {summary.get('total', 0)}.",
    ]
    for row in summary.get("kinds") or []:
        details.append(
            f"  {row['title']}: {row['count']} шт., суммарная длина ≈ {row['length_mm']:.0f} мм"
        )
    if summary.get("issues"):
        details.append("Замечания к начертанию (шаблон типа линии или толщина):")
        details.extend(f"  {note}" for note in summary["issues"][:12])
    return details
