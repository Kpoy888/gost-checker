from __future__ import annotations

from typing import Any

from ..constants import (
    FRAME_LEFT_MM,
    FRAME_OTHER_MM,
)
from ..frame_geometry import (
    all_formats,
    collect_rectangles,
    format_size,
    match_format,
)
from ..margin_report import format_margin_lines, margins_as_table_rows, margins_from_frames
from ..models import FrameInfo, Rect
from ..units import du_to_mm, mm_to_du
from .base import BaseCheck, CheckContext


def pick_frame(rects: list[Rect], to_mm: float) -> FrameInfo:
    info = FrameInfo()
    matched: list[tuple[Rect, str, str, str]] = []
    for rect in rects:
        hit = match_format(du_to_mm(rect.width, to_mm), du_to_mm(rect.height, to_mm))
        if hit:
            matched.append((rect, hit[0], hit[1], hit[2]))
    if not matched:
        return info

    # предпочтем пару outer+inner одного формата
    by_key: dict[tuple[str, str, str], list[Rect]] = {}
    for rect, name, orient, kind in matched:
        by_key.setdefault((name, orient, kind), []).append(rect)

    pairs = []
    for (name, orient, kind), items in by_key.items():
        if kind != "outer":
            continue
        inners = by_key.get((name, orient, "inner"), [])
        for outer in items:
            for inner in inners:
                if inner.space != outer.space:
                    continue
                if (
                    inner.xmin >= outer.xmin - mm_to_du(2, to_mm)
                    and inner.ymin >= outer.ymin - mm_to_du(2, to_mm)
                    and inner.xmax <= outer.xmax + mm_to_du(2, to_mm)
                    and inner.ymax <= outer.ymax + mm_to_du(2, to_mm)
                ):
                    paper = 0 if str(outer.space).lower() == "model" else 1
                    area = outer.width * outer.height
                    pairs.append((paper, area, outer, inner, name, orient))
    pairs.sort(reverse=True)
    best_pair = None
    if pairs:
        _paper, _area, outer, inner, name, orient = pairs[0]
        best_pair = (outer, inner, name, orient)

    if best_pair:
        outer, inner, name, orient = best_pair
        info.format_name = name
        info.orientation = orient
        info.kind = "both"
        info.outer = outer
        info.inner = inner
        info.space = outer.space
        return info

    # иначе берём самый крупный подходящий прямоугольник
    matched.sort(key=lambda item: item[0].width * item[0].height, reverse=True)
    rect, name, orient, kind = matched[0]
    info.format_name = name
    info.orientation = orient
    info.kind = kind
    info.space = rect.space
    if kind == "outer":
        info.outer = rect
    else:
        info.inner = rect
    return info


def ensure_frame_layers(doc: Any) -> None:
    if "SPDS_FRAME" not in doc.layers:
        doc.layers.add("SPDS_FRAME", linetype="Continuous", lineweight=70, color=7)


def add_sheet_frame(
    doc: Any,
    to_mm: float,
    fmt: str = "A3",
    orientation: str = "landscape",
    origin: tuple[float, float] = (0.0, 0.0),
    layout=None,
) -> tuple[Rect, Rect]:
    ensure_frame_layers(doc)
    w_mm, h_mm = format_size(fmt, orientation)
    ox, oy = origin
    w, h = mm_to_du(w_mm, to_mm), mm_to_du(h_mm, to_mm)
    left, other = mm_to_du(FRAME_LEFT_MM, to_mm), mm_to_du(FRAME_OTHER_MM, to_mm)
    target = layout if layout is not None else doc.modelspace()
    outer_pts = [
        (ox, oy),
        (ox + w, oy),
        (ox + w, oy + h),
        (ox, oy + h),
    ]
    inner_pts = [
        (ox + left, oy + other),
        (ox + w - other, oy + other),
        (ox + w - other, oy + h - other),
        (ox + left, oy + h - other),
    ]
    target.add_lwpolyline(outer_pts, close=True, dxfattribs={"layer": "SPDS_FRAME", "lineweight": 25})
    target.add_lwpolyline(inner_pts, close=True, dxfattribs={"layer": "SPDS_FRAME", "lineweight": 70})
    outer = Rect(ox, oy, ox + w, oy + h, space=getattr(target, "name", "Model"))
    inner = Rect(inner_pts[0][0], inner_pts[0][1], inner_pts[1][0], inner_pts[2][1], space=outer.space)
    return outer, inner


def add_inner_for_outer(doc: Any, outer: Rect, to_mm: float, layout=None) -> Rect:
    ensure_frame_layers(doc)
    left, other = mm_to_du(FRAME_LEFT_MM, to_mm), mm_to_du(FRAME_OTHER_MM, to_mm)
    target = layout if layout is not None else doc.modelspace()
    pts = [
        (outer.xmin + left, outer.ymin + other),
        (outer.xmax - other, outer.ymin + other),
        (outer.xmax - other, outer.ymax - other),
        (outer.xmin + left, outer.ymax - other),
    ]
    target.add_lwpolyline(pts, close=True, dxfattribs={"layer": "SPDS_FRAME", "lineweight": 70})
    return Rect(pts[0][0], pts[0][1], pts[1][0], pts[2][1], space=outer.space)


class SheetFrameCheck(BaseCheck):
    id = "sheet_frame"
    title = "Формат листа и рамка"
    gost = "ГОСТ 2.301-68; поля рамки — по общепринятому оформлению (20 мм слева, 5 мм с остальных сторон), см. также ГОСТ Р 21.101-2020 прил. И"
    can_autofix = True

    def run(self, ctx: CheckContext):
        rects = collect_rectangles(ctx.doc, ctx.to_mm)
        info = pick_frame(rects, ctx.to_mm)
        details: list[str] = []
        details.append(
            f"Просматривались пространства модели и листов; найдено прямоугольников-кандидатов: {len(rects)}."
        )

        if info.format_name:
            details.append(
                f"Определён формат {info.format_name} ({info.orientation or '—'}), "
                f"источник: {info.kind}, пространство: {info.space}."
            )
            if info.outer:
                details.append(
                    f"Внешняя рамка: {du_to_mm(info.outer.width, ctx.to_mm):.1f}×"
                    f"{du_to_mm(info.outer.height, ctx.to_mm):.1f} мм."
                )
            if info.inner:
                details.append(
                    f"Внутренняя рамка: {du_to_mm(info.inner.width, ctx.to_mm):.1f}×"
                    f"{du_to_mm(info.inner.height, ctx.to_mm):.1f} мм."
                )
            if info.outer and info.inner:
                margins = margins_from_frames(info.outer, info.inner, ctx.to_mm)
                info.margins = margins
                ctx.extras["margins"] = margins
                details.extend(format_margin_lines(margins))
                extra = {"margins": margins, "margins_table": margins_as_table_rows(margins),
                         "format": info.format_name, "orientation": info.orientation}
                offsets_ok = margins["all_ok"] or (
                    abs(margins["left"]["value"] - FRAME_LEFT_MM) <= 2.5
                    and abs(margins["right"]["value"] - FRAME_OTHER_MM) <= 2.5
                    and abs(margins["top"]["value"] - FRAME_OTHER_MM) <= 2.5
                    and abs(margins["bottom"]["value"] - FRAME_OTHER_MM) <= 2.5
                )
                if not margins["all_ok"]:
                    ctx.frame = info
                    sev = "warning"
                    msg = (
                        "Рамка найдена, но поля отличаются от 20 мм слева и 5 мм с остальных сторон "
                        "(допуск отчёта ±0,5 мм)."
                    )
                    if offsets_ok:
                        msg += " Отклонение небольшое; существующую геометрию инструмент не двигает."
                    else:
                        msg += " Существующую геометрию инструмент не двигает — это небезопасно."
                    return self.result(sev, msg, details, findings_count=1, extra=extra)
                ctx.frame = info
                return self.result(
                    "ok",
                    f"Найдены внешняя и внутренняя рамки формата {info.format_name}, поля 20/5/5/5 мм в допуске.",
                    details,
                    extra=extra,
                )

            if info.inner and not info.outer:
                ctx.frame = info
                return self.result(
                    "info",
                    f"Найдена внутренняя рамка формата {info.format_name}. "
                    "Внешняя (обрезной край листа) не обнаружена — в CAD её часто не рисуют.",
                    details,
                )

            if info.outer and not info.inner:
                if ctx.fix:
                    info.inner = add_inner_for_outer(ctx.doc, info.outer, ctx.to_mm)
                    info.kind = "both"
                    ctx.frame = info
                    margins = margins_from_frames(info.outer, info.inner, ctx.to_mm)
                    info.margins = margins
                    ctx.extras["margins"] = margins
                    details.append("Добавлена внутренняя рамка с полями 20 мм слева и 5 мм с остальных сторон.")
                    details.extend(format_margin_lines(margins))
                    extra = {"margins": margins, "margins_table": margins_as_table_rows(margins)}
                    return self.result(
                        "warning",
                        "Была только внешняя рамка; внутренняя рамка добавлена автоматически.",
                        details,
                        auto_fixed=True,
                        findings_count=1,
                        extra=extra,
                    )
                ctx.frame = info
                return self.result(
                    "warning",
                    "Найдена внешняя рамка формата, но нет внутренней. Можно добавить при --fix.",
                    details,
                    findings_count=1,
                )

        # рамки нет
        assumed = ctx.assumed_format.upper() if ctx.assumed_format else "A3"
        if assumed not in all_formats():
            assumed = "A3"
        orientation = "portrait" if assumed == "A4" else "landscape"
        details.append(
            f"Стандартный формат по размерам А0–А4 (и дополнительным) не распознан. "
            f"При исправлении будет добавлен лист {assumed} ({orientation})."
        )
        if ctx.fix:
            outer, inner = add_sheet_frame(ctx.doc, ctx.to_mm, fmt=assumed, orientation=orientation)
            info = FrameInfo(
                format_name=assumed,
                orientation=orientation,
                kind="both",
                outer=outer,
                inner=inner,
                space=outer.space,
            )
            ctx.frame = info
            margins = margins_from_frames(outer, inner, ctx.to_mm)
            info.margins = margins
            ctx.extras["margins"] = margins
            details.extend(format_margin_lines(margins))
            details.append(
                f"Добавлены внешняя ({du_to_mm(outer.width, ctx.to_mm):.0f}×"
                f"{du_to_mm(outer.height, ctx.to_mm):.0f} мм) и внутренняя рамки."
            )
            return self.result(
                "warning",
                f"Рамка отсутствовала и добавлена по формату {assumed}. "
                "Если после вставки рамки и штампа объекты окажутся на основной надписи, "
                "проверка наложений сдвинет их в рабочее поле.",
                details,
                auto_fixed=True,
                findings_count=1,
            )
        ctx.frame = info
        return self.result(
            "error",
            "Рамка листа не найдена. Запустите с --fix, чтобы добавить рамку выбранного формата.",
            details,
            findings_count=1,
        )
