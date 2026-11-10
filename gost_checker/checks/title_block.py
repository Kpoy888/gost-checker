from __future__ import annotations

from ..constants import (
    TITLE_BLOCK_H_MM,
    TITLE_BLOCK_NAME,
    TITLE_BLOCK_TOLERANCE_MM,
    TITLE_BLOCK_W_MM,
    TITLE_KEYWORDS,
)
from ..titleblock import insert_form3, title_block_insert_point
from ..units import du_to_mm, mm_to_du
from ..models import Rect
from .base import BaseCheck, CheckContext
from .sheet_frame import collect_rectangles
from ..stamp_cells import field_details, fields_from_inserts, find_extra_zones


def _looks_like_title_block(rect: Rect, to_mm: float) -> bool:
    w = du_to_mm(rect.width, to_mm)
    h = du_to_mm(rect.height, to_mm)
    return (
        abs(w - TITLE_BLOCK_W_MM) <= TITLE_BLOCK_TOLERANCE_MM
        and abs(h - TITLE_BLOCK_H_MM) <= TITLE_BLOCK_TOLERANCE_MM
    )


def _near_bottom_right(rect: Rect, frame_inner: Rect | None, to_mm: float) -> bool:
    if frame_inner is None:
        return True
    tol = mm_to_du(12, to_mm)
    return (
        abs(rect.xmax - frame_inner.xmax) <= tol
        and abs(rect.ymin - frame_inner.ymin) <= tol
    )


class TitleBlockCheck(BaseCheck):
    id = "title_block"
    title = "Основная надпись (штамп)"
    gost = "ГОСТ Р 21.101-2020, форма 3 (185×55 мм) для листов основных комплектов рабочих чертежей и графических документов проектной документации"
    can_autofix = True

    def run(self, ctx: CheckContext):
        details: list[str] = []
        frame = ctx.frame
        inner = frame.inner if frame else None

        # Блоки-вставки
        block_hits = []
        keywords = ("SPDS_F3", "FORM3", "F3", "STAMP", "TITLE", "SHTAMP", "OSN_NADP", "21.101", "21101")
        for space_name, layout in (("Model", ctx.doc.modelspace()),):
            pass
        layouts = [("Model", ctx.doc.modelspace())]
        for layout in ctx.doc.layouts:
            if layout.name.lower() != "model":
                layouts.append((layout.name, layout))
        for space_name, layout in layouts:
            for insert in layout.query("INSERT"):
                name = (insert.dxf.name or "").upper()
                if any(key in name for key in keywords) or TITLE_BLOCK_NAME.upper() in name:
                    block_hits.append((space_name, insert.dxf.name, insert.dxf.insert))
                # атрибуты с характерными тегами
                try:
                    tags = " ".join(a.dxf.tag.upper() for a in insert.attribs)
                except Exception:
                    tags = ""
                if any(tag in tags for tag in ("OBOZN", "STADIYA", "RAZRAB", "LISTOV", "ОБОЗН")):
                    block_hits.append((space_name, insert.dxf.name, insert.dxf.insert))

        if block_hits:
            details.append("Найдены вхождения блоков, похожих на основную надпись:")
            for space_name, name, insert in block_hits[:8]:
                details.append(f"  блок «{name}» в {space_name}, точка вставки {insert}")
            fields = fields_from_inserts(ctx.doc)
            lines, empty_req, extra = field_details(fields)
            details.extend(lines)
            rects = collect_rectangles(ctx.doc, ctx.to_mm)
            stamp_guess = next((r for r in rects if _looks_like_title_block(r, ctx.to_mm)), None)
            zones = find_extra_zones(rects, stamp_guess, inner, ctx.to_mm) if stamp_guess else []
            if zones:
                details.append("Дополнительные графические зоны около штампа:")
                details.extend(f"  {z['where']}: {z['width_mm']:.0f}×{z['height_mm']:.0f} мм" for z in zones)
                extra["extra_zones"] = zones
            else:
                details.append("Отдельных дополнительных граф слева или над штампом не обнаружено.")
            ctx.extras["stamp_fields"] = extra
            if empty_req:
                return self.result(
                    "warning",
                    "Основная надпись найдена, но не заполнены обязательные графы: " + ", ".join(empty_req) + ".",
                    details,
                    findings_count=len(empty_req),
                    extra=extra,
                )
            return self.result(
                "ok",
                "Основная надпись обнаружена (блок с характерным именем или атрибутами), обязательные графы заполнены.",
                details,
                extra=extra,
            )

        # Прямоугольник 185×55
        rects = collect_rectangles(ctx.doc, ctx.to_mm)
        stamp_rects = [r for r in rects if _looks_like_title_block(r, ctx.to_mm)]
        if inner:
            near = [r for r in stamp_rects if _near_bottom_right(r, inner, ctx.to_mm)]
            if near:
                stamp_rects = near
        if stamp_rects:
            r0 = stamp_rects[0]
            details.append(
                f"Найден прямоугольник {du_to_mm(r0.width, ctx.to_mm):.1f}×"
                f"{du_to_mm(r0.height, ctx.to_mm):.1f} мм "
                f"(ожидается 185×55), пространство {r0.space}."
            )
            # есть ли подписи граф
            text_blob = " ".join(_collect_text(ctx.doc)).lower()
            found_kw = [k for k in TITLE_KEYWORDS if k in text_blob]
            if found_kw:
                details.append("В чертеже есть характерные подписи граф: " + ", ".join(found_kw) + ".")
            else:
                details.append(
                    "Прямоугольник размера формы 3 есть, но подписи граф (Разраб., Стадия и др.) не найдены. "
                    "Похоже на контур штампа без заполнения."
                )
            fields = fields_from_inserts(ctx.doc)
            lines, empty_req, extra = field_details(fields)
            details.extend(lines)
            zones = find_extra_zones(rects, r0, inner, ctx.to_mm)
            if zones:
                details.append("Дополнительные графические зоны около штампа:")
                details.extend(f"  {z['where']}: {z['width_mm']:.0f}×{z['height_mm']:.0f} мм" for z in zones)
                extra["extra_zones"] = zones
            ctx.extras["stamp_fields"] = extra
            if empty_req:
                return self.result(
                    "warning",
                    "Контур штампа 185×55 мм найден, обязательные графы пусты: " + ", ".join(empty_req) + ".",
                    details,
                    findings_count=len(empty_req),
                    extra=extra,
                )
            return self.result("ok", "Обнаружен контур основной надписи формата 185×55 мм.", details, extra=extra)

        text_blob = " ".join(_collect_text(ctx.doc)).lower()
        found_kw = [k for k in TITLE_KEYWORDS if k in text_blob]
        if found_kw:
            details.append("Подписи граф встречаются в тексте (" + ", ".join(found_kw) + "), но контур 185×55 мм не найден.")

        details.append(
            "Искомая форма 3: 185×55 мм в правом нижнем углу внутренней рамки. "
            "Внутренняя сетка вставляемого штампа — практическая реконструкция граф "
            "(обозначение, наименование, стадия, лист, листов, подписи, организация)."
        )

        if ctx.fix:
            if inner is None:
                # запасной вариант: начало координат, формат по умолчанию как у рамки
                inner = None
                if frame and frame.outer:
                    from ..constants import FRAME_LEFT_MM as FL, FRAME_OTHER_MM as FO
                    inner = Rect(
                        frame.outer.xmin + mm_to_du(FL, ctx.to_mm),
                        frame.outer.ymin + mm_to_du(FO, ctx.to_mm),
                        frame.outer.xmax - mm_to_du(FO, ctx.to_mm),
                        frame.outer.ymax - mm_to_du(FO, ctx.to_mm),
                        space=frame.outer.space,
                    )
            if inner is None:
                # если рамки нет совсем — вставим в (20, 5) относительно (0,0) как для А3 альбом
                from ..constants import SHEET_FORMATS, FRAME_LEFT_MM as FL, FRAME_OTHER_MM as FO
                w, h = SHEET_FORMATS.get(ctx.assumed_format.upper(), SHEET_FORMATS["A3"])
                if ctx.assumed_format.upper() != "A4":
                    w, h = max(w, h), min(w, h)
                insert = (mm_to_du(FL + (w - FL - FO) - TITLE_BLOCK_W_MM, ctx.to_mm), mm_to_du(FO, ctx.to_mm))
                space = "Model"
                layout = ctx.doc.modelspace()
            else:
                insert = title_block_insert_point(inner.xmin, inner.ymin, inner.xmax, ctx.to_mm)
                space = inner.space
                layout = ctx.doc.modelspace() if space.lower() == "model" else ctx.doc.layouts.get(space)
            if "SPDS_STAMP" not in ctx.doc.layers:
                ctx.doc.layers.add("SPDS_STAMP", linetype="Continuous", lineweight=50, color=7)
            insert_form3(ctx.doc, insert, ctx.title_meta, to_mm=ctx.to_mm, layout=layout)
            details.append(f"Вставлена основная надпись формы 3 в точку ({insert[0]:.1f}, {insert[1]:.1f}), пространство {space}.")
            fields = fields_from_inserts(ctx.doc)
            lines, empty_req, extra = field_details(fields)
            details.extend(lines)
            ctx.extras["stamp_fields"] = extra
            msg = (
                "Основная надпись отсутствовала и вставлена автоматически (форма 3, 185×55 мм). "
                "Проверьте заполнение граф и точное совпадение сетки с вашим шаблоном кафедры."
            )
            if empty_req:
                msg += " Пустые обязательные графы: " + ", ".join(empty_req) + "."
            return self.result(
                "warning",
                msg,
                details,
                auto_fixed=True,
                findings_count=1,
                extra=extra,
            )

        return self.result(
            "error",
            "Основная надпись формы 3 (185×55 мм) не найдена. При --fix штамп будет вставлен в правый нижний угол рамки.",
            details,
            findings_count=1,
        )


def _collect_text(doc) -> list[str]:
    texts: list[str] = []
    for layout in [doc.modelspace(), *list(doc.layouts)]:
        try:
            entities = list(layout)
        except Exception:
            continue
        for entity in entities:
            dtype = entity.dxftype()
            if dtype == "TEXT":
                texts.append(str(entity.dxf.text or ""))
            elif dtype == "MTEXT":
                try:
                    texts.append(entity.plain_text())
                except Exception:
                    texts.append(str(entity.dxf.text or ""))
            elif dtype == "ATTRIB" or dtype == "ATTDEF":
                texts.append(str(entity.dxf.text or ""))
                texts.append(str(getattr(entity.dxf, "tag", "") or ""))
            elif dtype == "INSERT":
                for attrib in getattr(entity, "attribs", []):
                    texts.append(str(attrib.dxf.text or ""))
                    texts.append(str(attrib.dxf.tag or ""))
    return texts
