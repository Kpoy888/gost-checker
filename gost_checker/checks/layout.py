"""Наложение содержимого на основную надпись и сдвиг в рабочее поле."""

from __future__ import annotations

from typing import Optional

from ezdxf.math import Matrix44

from ..constants import FRAME_LEFT_MM, FRAME_OTHER_MM, TITLE_BLOCK_H_MM, TITLE_BLOCK_NAME, TITLE_BLOCK_W_MM
from ..models import Rect
from ..units import mm_to_du
from .base import BaseCheck, CheckContext

PROTECTED_LAYERS = {"SPDS_FRAME", "SPDS_STAMP", "DEFPOINTS"}


def _entity_rect(entity) -> Optional[Rect]:
    try:
        from ezdxf import bbox as ezbbox

        ext = ezbbox.extents([entity], fast=True)
        if ext is not None and getattr(ext, "has_data", True):
            xmin, ymin = float(ext.extmin.x), float(ext.extmin.y)
            xmax, ymax = float(ext.extmax.x), float(ext.extmax.y)
            if xmax >= xmin and ymax >= ymin:
                return Rect(xmin, ymin, xmax, ymax)
    except Exception:
        pass
    dtype = entity.dxftype()
    try:
        if dtype == "TEXT":
            ins = entity.dxf.insert
            height = float(entity.dxf.height or 2.5)
            width = max(height * 0.62 * len(entity.dxf.text or ""), height)
            return Rect(float(ins.x), float(ins.y), float(ins.x) + width, float(ins.y) + height)
        if dtype == "MTEXT":
            ins = entity.dxf.insert
            height = float(entity.dxf.char_height or 2.5)
            try:
                text = entity.plain_text()
            except Exception:
                text = str(entity.dxf.text or "")
            width = max(height * 0.62 * len(text), height)
            return Rect(float(ins.x), float(ins.y) - height, float(ins.x) + width, float(ins.y))
        if dtype == "LINE":
            x1, y1 = float(entity.dxf.start.x), float(entity.dxf.start.y)
            x2, y2 = float(entity.dxf.end.x), float(entity.dxf.end.y)
            return Rect(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        if dtype == "CIRCLE":
            center = entity.dxf.center
            radius = float(entity.dxf.radius)
            return Rect(center.x - radius, center.y - radius, center.x + radius, center.y + radius)
    except Exception:
        return None
    return None


def interiors_overlap(a: Rect, b: Rect, eps: float = 0.3) -> bool:
    return not (
        a.xmax <= b.xmin + eps
        or a.xmin >= b.xmax - eps
        or a.ymax <= b.ymin + eps
        or a.ymin >= b.ymax - eps
    )


def _same_rect(a: Rect, b: Rect, tol: float) -> bool:
    return (
        abs(a.xmin - b.xmin) <= tol
        and abs(a.ymin - b.ymin) <= tol
        and abs(a.xmax - b.xmax) <= tol
        and abs(a.ymax - b.ymax) <= tol
    )


def is_protected(entity, ctx: CheckContext) -> bool:
    layer = str(entity.dxf.layer or "")
    if layer.upper() in PROTECTED_LAYERS:
        return True
    if entity.dxftype() == "INSERT":
        name = str(entity.dxf.name or "").upper()
        if TITLE_BLOCK_NAME.upper() in name or "SPDS_F3" in name:
            return True
    rect = _entity_rect(entity)
    frame = ctx.frame
    if rect and frame:
        tol = mm_to_du(2.5, ctx.to_mm)
        for candidate in (frame.outer, frame.inner):
            if candidate and _same_rect(rect, candidate, tol):
                return True
    return False


def describe(entity) -> str:
    dtype = entity.dxftype()
    if dtype == "TEXT":
        return f"TEXT «{(entity.dxf.text or '')[:48]}»"
    if dtype == "MTEXT":
        try:
            text = entity.plain_text()
        except Exception:
            text = str(entity.dxf.text or "")
        return f"MTEXT «{text[:48]}»"
    return f"{dtype} на слое «{entity.dxf.layer}»"


def assumed_inner(ctx: CheckContext) -> Optional[Rect]:
    if ctx.frame and ctx.frame.inner:
        return ctx.frame.inner
    if ctx.frame and ctx.frame.outer:
        outer = ctx.frame.outer
        left = mm_to_du(FRAME_LEFT_MM, ctx.to_mm)
        other = mm_to_du(FRAME_OTHER_MM, ctx.to_mm)
        return Rect(outer.xmin + left, outer.ymin + other, outer.xmax - other, outer.ymax - other, space=outer.space)
    from .sheet_frame import format_size

    assumed = (ctx.assumed_format or "A3").upper()
    orientation = "portrait" if assumed == "A4" else "landscape"
    try:
        width_mm, height_mm = format_size(assumed, orientation)
    except Exception:
        return None
    left = mm_to_du(FRAME_LEFT_MM, ctx.to_mm)
    other = mm_to_du(FRAME_OTHER_MM, ctx.to_mm)
    width = mm_to_du(width_mm, ctx.to_mm)
    height = mm_to_du(height_mm, ctx.to_mm)
    return Rect(left, other, width - other, height - other)


def stamp_zone(ctx: CheckContext) -> Optional[Rect]:
    layout = _layout(ctx)
    for insert in layout.query("INSERT"):
        name = str(insert.dxf.name or "").upper()
        if TITLE_BLOCK_NAME.upper() in name or "SPDS_F3" in name:
            rect = _entity_rect(insert)
            if rect:
                return rect
    inner = assumed_inner(ctx)
    if inner is None:
        return None
    width = mm_to_du(TITLE_BLOCK_W_MM, ctx.to_mm)
    height = mm_to_du(TITLE_BLOCK_H_MM, ctx.to_mm)
    return Rect(inner.xmax - width, inner.ymin, inner.xmax, inner.ymin + height, space=inner.space)


def working_field(ctx: CheckContext, stamp: Rect) -> Optional[Rect]:
    inner = assumed_inner(ctx)
    if inner is None:
        return None
    pad = mm_to_du(5.0, ctx.to_mm)
    return Rect(
        inner.xmin + pad,
        max(stamp.ymax, inner.ymin) + pad,
        inner.xmax - pad,
        inner.ymax - pad,
        space=inner.space,
    )


def _layout(ctx: CheckContext):
    space = (ctx.frame.space if ctx.frame else "Model") or "Model"
    if str(space).lower() == "model":
        return ctx.doc.modelspace()
    try:
        return ctx.doc.layouts.get(space)
    except Exception:
        return ctx.doc.modelspace()


def union_rects(rects: list[Rect]) -> Optional[Rect]:
    if not rects:
        return None
    return Rect(
        min(item.xmin for item in rects),
        min(item.ymin for item in rects),
        max(item.xmax for item in rects),
        max(item.ymax for item in rects),
    )


def _move_entity(entity, dx: float, dy: float) -> bool:
    dtype = entity.dxftype()
    try:
        if dtype in {"TEXT", "MTEXT", "INSERT", "CIRCLE", "ARC", "POINT"}:
            if dtype == "POINT":
                point = entity.dxf.location
                entity.dxf.location = (point.x + dx, point.y + dy, getattr(point, "z", 0.0))
                return True
            if dtype in {"CIRCLE", "ARC"}:
                point = entity.dxf.center
                entity.dxf.center = (point.x + dx, point.y + dy, getattr(point, "z", 0.0))
                return True
            point = entity.dxf.insert
            entity.dxf.insert = (point.x + dx, point.y + dy, getattr(point, "z", 0.0))
            return True
        matrix = Matrix44.translate(dx, dy, 0)
        entity.transform(matrix)
        return True
    except Exception:
        return False


def fit_content(content: Rect, free: Rect) -> tuple[Matrix44, float]:
    scale = min(free.width / max(content.width, 1e-9), free.height / max(content.height, 1e-9), 1.0)
    new_w = content.width * scale
    new_h = content.height * scale
    tx = free.xmin + (free.width - new_w) / 2.0
    ty = free.ymin + (free.height - new_h) / 2.0
    matrix = Matrix44.chain(
        Matrix44.translate(-content.xmin, -content.ymin, 0),
        Matrix44.scale(scale),
        Matrix44.translate(tx, ty, 0),
    )
    return matrix, scale


class LayoutOverlapCheck(BaseCheck):
    id = "layout_overlap"
    title = "Наложение на основную надпись"
    gost = "ГОСТ Р 21.101-2020 п. 5.2.1: основная надпись в правом нижнем углу; рабочее поле листа не должно пересекаться со штампом"
    can_autofix = True

    def run(self, ctx: CheckContext):
        details: list[str] = []
        stamp = stamp_zone(ctx)
        if stamp is None:
            return self.result(
                "info",
                "Зону основной надписи определить не удалось — проверку наложений пропустили.",
                details,
            )
        details.append(
            f"Зона штампа: ({stamp.xmin:.1f}, {stamp.ymin:.1f})–({stamp.xmax:.1f}, {stamp.ymax:.1f})."
        )
        layout = _layout(ctx)
        items: list[tuple] = []
        for entity in layout:
            if is_protected(entity, ctx):
                continue
            rect = _entity_rect(entity)
            if rect is None:
                continue
            items.append((entity, rect))

        overlapping = [(entity, rect) for entity, rect in items if interiors_overlap(rect, stamp)]
        if not overlapping:
            return self.result("ok", "Содержимое чертежа не пересекается с основной надписью.", details)

        details.append("Пересекают зону штампа:")
        details.extend(f"  {describe(entity)}" for entity, _rect in overlapping[:20])
        texts = [pair for pair in overlapping if pair[0].dxftype() in {"TEXT", "MTEXT"}]
        geometry = [pair for pair in overlapping if pair[0].dxftype() not in {"TEXT", "MTEXT"}]
        free = working_field(ctx, stamp)
        content_box = union_rects([rect for _entity, rect in items])

        if not ctx.fix:
            hint = "При --fix "
            if geometry or (free and content_box and (content_box.width > free.width or content_box.height > free.height)):
                hint += "всё содержимое (кроме рамки и штампа) будет сдвинуто в рабочее поле над штампом с сохранением пропорций."
            else:
                hint += "пересекающие штамп надписи будут сдвинуты из его зоны."
            details.append(hint)
            return self.result(
                "warning",
                f"Обнаружено наложений на штамп: {len(overlapping)}. Исправление доступно с --fix.",
                details,
                findings_count=len(overlapping),
            )

        need_refit = bool(geometry) or (
            free is not None
            and content_box is not None
            and (content_box.width > free.width + 1 or content_box.height > free.height + 1)
        )
        moved: list[str] = []
        if need_refit and free is not None and content_box is not None and free.width > 1 and free.height > 1:
            matrix, scale = fit_content(content_box, free)
            ok = 0
            fail = 0
            for entity, _rect in items:
                try:
                    entity.transform(matrix)
                    ok += 1
                    moved.append(describe(entity))
                except Exception:
                    fail += 1
            details.append(
                f"Содержимое модели перенесено в рабочее поле над штампом "
                f"(коэффициент масштаба {scale:.3f}, пропорции сохранены). "
                f"Преобразовано объектов: {ok}" + (f", не удалось: {fail}" if fail else "") + "."
            )
            details.append("Перемещено: " + "; ".join(moved[:15]) + ("…" if len(moved) > 15 else ""))
            return self.result(
                "warning",
                "Содержимое пересекало штамп и было целиком сдвинуто в рабочее поле рамки.",
                details,
                auto_fixed=ok > 0,
                findings_count=len(overlapping),
            )

        # Только надписи: сдвигаем каждую вверх (или влево, если вверх не помещается).
        pad = mm_to_du(3.0, ctx.to_mm)
        inner = ctx.frame.inner if ctx.frame else None
        for entity, rect in texts:
            dy = (stamp.ymax + pad) - rect.ymin
            dx = 0.0
            new_top = rect.ymax + dy
            if inner is not None and new_top > inner.ymax - pad:
                dx = (stamp.xmin - pad) - rect.xmax
                dy = 0.0
            if _move_entity(entity, dx, dy):
                moved.append(f"{describe(entity)} → смещение ({dx:.1f}, {dy:.1f})")
        if moved:
            details.append("Сдвинуты только пересекающие штамп надписи:")
            details.extend(f"  {item}" for item in moved)
            return self.result(
                "warning",
                "Надписи, пересекавшие основную надпись, сдвинуты из её зоны.",
                details,
                auto_fixed=True,
                findings_count=len(overlapping),
            )
        return self.result(
            "warning",
            "Наложения на штамп обнаружены, но автоматически сдвинуть объекты не удалось.",
            details,
            findings_count=len(overlapping),
        )
