from __future__ import annotations

import re

from ..constants import (
    FONT_HEIGHTS_PRIMARY_MM,
    GOST_FONT_FILE,
    GOST_FONT_MARKERS,
    NON_GOST_FONT_MARKERS,
)
from ..titleblock import ensure_gost_style
from ..units import du_to_mm, mm_to_du
from .base import BaseCheck, CheckContext


def is_gost_font(name: str) -> bool:
    n = (name or "").strip().lower().replace("\\", "/")
    # берём только имя файла
    n = n.split("/")[-1]
    if not n:
        return False
    return any(marker in n for marker in GOST_FONT_MARKERS)


def is_explicit_non_gost(name: str) -> bool:
    n = (name or "").strip().lower()
    return any(marker in n for marker in NON_GOST_FONT_MARKERS)


def nearest_height(height_mm: float) -> float:
    return min(FONT_HEIGHTS_PRIMARY_MM, key=lambda item: abs(item - height_mm))


def _style_font(style) -> str:
    try:
        return str(style.dxf.get("font") or "")
    except Exception:
        return ""


MTEXT_H_RE = re.compile(r"\\H([0-9]+(?:[.,][0-9]+)?)(x)?;", re.IGNORECASE)
DRAWING_TEXT_MAX_MM = 7.0


def style_fixed_height(doc, style_name: str) -> float:
    if not style_name:
        return 0.0
    try:
        style = doc.styles.get(style_name)
        return float(style.dxf.height or 0)
    except Exception:
        return 0.0


def mtext_inline_heights(raw: str, base: float) -> list[float]:
    heights = []
    for match in MTEXT_H_RE.finditer(raw or ""):
        value = float(match.group(1).replace(",", "."))
        if match.group(2):
            heights.append(base * value)
        else:
            heights.append(value)
    return heights


def rewrite_mtext_heights(raw: str, snap) -> str:
    def repl(match: re.Match) -> str:
        if match.group(2):
            return match.group(0)
        value = float(match.group(1).replace(",", "."))
        target = snap(value)
        return f"\\H{target:g};"

    return MTEXT_H_RE.sub(repl, raw or "")


def snap_drawing_height(height_mm: float, *, allow_large: bool = False) -> float:
    target = nearest_height(height_mm)
    if not allow_large and height_mm > DRAWING_TEXT_MAX_MM + 0.12 and target > DRAWING_TEXT_MAX_MM:
        return DRAWING_TEXT_MAX_MM
    return target


def _entity_height(entity, doc=None) -> float | None:
    dtype = entity.dxftype()
    if dtype == "TEXT":
        height = float(entity.dxf.height or 0)
        if height <= 1e-9 and doc is not None:
            height = style_fixed_height(doc, str(entity.dxf.style or ""))
        return height or None
    if dtype == "MTEXT":
        return float(entity.dxf.char_height or 0)
    if dtype in {"ATTRIB", "ATTDEF"}:
        height = float(entity.dxf.height or 0)
        if height <= 1e-9 and doc is not None:
            height = style_fixed_height(doc, str(entity.dxf.style or ""))
        return height or None
    return None


def _set_entity_height(entity, value: float) -> None:
    dtype = entity.dxftype()
    if dtype == "TEXT":
        entity.dxf.height = value
    elif dtype == "MTEXT":
        entity.dxf.char_height = value
    elif dtype in {"ATTRIB", "ATTDEF"}:
        entity.dxf.height = value


def _iter_text_entities(doc):
    layouts = [doc.modelspace(), *list(doc.layouts)]
    seen = set()
    for layout in layouts:
        ident = id(layout)
        if ident in seen:
            continue
        seen.add(ident)
        for entity in layout:
            dtype = entity.dxftype()
            if dtype in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}:
                yield entity
            elif dtype == "INSERT":
                for attrib in getattr(entity, "attribs", []):
                    yield attrib
    for block in doc.blocks:
        name = str(block.name or "")
        if name.lower() in {"*model_space", "*paper_space"} or name.lower().startswith("*paper_space"):
            continue
        ident = id(block)
        if ident in seen:
            continue
        seen.add(ident)
        for entity in block:
            if entity.dxftype() in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}:
                yield entity


class FontsCheck(BaseCheck):
    id = "fonts"
    title = "Шрифты и высоты текста"
    gost = "ГОСТ 2.304-81 (чертёжные шрифты типа А/Б; ряд высот 1,8; 2,5; 3,5; 5; 7; 10; 14; 20 мм)"
    can_autofix = True

    def run(self, ctx: CheckContext):
        details: list[str] = []
        bad_styles = []
        unknown_styles = []
        height_issues = []

        used = set()
        for entity in _iter_text_entities(ctx.doc):
            try:
                style_name = entity.dxf.style
            except Exception:
                style_name = None
            if style_name:
                used.add(style_name)
        if used:
            details.append("Проверяются только стили, которыми написан текст на чертеже: " + ", ".join(sorted(used)) + ".")
        else:
            details.append("Текстовых объектов не найдено — стили шаблона не проверялись.")

        for style in ctx.doc.styles:
            name = style.dxf.name
            if name not in used:
                continue
            font = _style_font(style)
            if is_gost_font(name) or is_gost_font(font):
                continue
            if is_explicit_non_gost(name) or is_explicit_non_gost(font):
                bad_styles.append((name, font or "—"))
            elif font:
                unknown_styles.append((name, font))
            else:
                # пустой шрифт у Standard обычно означает txt.shx — это не ГОСТ 2.304
                if name.lower() == "standard":
                    unknown_styles.append((name, font or "системный (часто txt.shx)"))

        for entity in _iter_text_entities(ctx.doc):
            raw = _entity_height(entity, ctx.doc)
            if raw is None or raw <= 0:
                continue
            h_mm = du_to_mm(raw, ctx.to_mm)
            if h_mm < 1.0 or h_mm > 45.0:
                height_issues.append((entity, h_mm, None, "entity"))
                continue
            target = snap_drawing_height(h_mm)
            if abs(target - h_mm) > 0.12:
                height_issues.append((entity, h_mm, target, "entity"))
            if entity.dxftype() == "MTEXT":
                raw_text = str(entity.dxf.text or "")
                for inline in mtext_inline_heights(raw_text, h_mm):
                    inline_mm = du_to_mm(inline, ctx.to_mm) if inline > 20 else inline
                    # inline \H обычно уже в единицах чертежа или в мм при INSUNITS=мм
                    if inline > 0.5 and inline < 50:
                        inline_mm = inline if inline < 45 else du_to_mm(inline, ctx.to_mm)
                        target_i = snap_drawing_height(inline_mm)
                        if abs(target_i - inline_mm) > 0.12:
                            height_issues.append((entity, inline_mm, target_i, "inline"))

        if bad_styles:
            details.append("Стили с явно не ГОСТ-шрифтом (Arial, Times и т.п.):")
            details.extend(f"  стиль «{n}», файл «{f}»" for n, f in bad_styles)
        if unknown_styles:
            details.append("Стили без признаков шрифта ГОСТ 2.304 / ISOCPEUR:")
            details.extend(f"  стиль «{n}», файл «{f}»" for n, f in unknown_styles[:12])
        if not bad_styles and not unknown_styles:
            details.append("Текстовые стили используют имена, похожие на ГОСТ type A/B или ISOCPEUR.")

        roundable = [(e, h, t, k) for e, h, t, k in height_issues if t is not None]
        skipped = [(e, h, t, k) for e, h, t, k in height_issues if t is None]
        if roundable:
            details.append("Высоты текста вне ряда ГОСТ 2.304:")
            for entity, h, t, _kind in roundable[:15]:
                details.append(f"  {entity.dxftype()} {h:.2f} мм → {t:.1f} мм")
        if skipped:
            details.append(
                f"Пропущены {len(skipped)} надписей с высотой вне разумного диапазона 1–45 мм "
                "(возможна ошибка единиц измерения)."
            )
        if not height_issues:
            details.append("Высоты текста совпадают с рядом 1,8; 2,5; 3,5; 5; 7; 10; 14; 20 мм.")

        details.append(
            "Исправление задаёт стиль GOST_A с именем шрифта «GOST type A». "
            "Сам файл .shx/.ttf в чертёж не встраивается: его нужно установить в CAD "
            "(nanoCAD / LibreCAD / AutoCAD-совместимые шрифты ГОСТ)."
        )

        auto = False
        if ctx.fix:
            ensure_gost_style(ctx.doc)
            for style in ctx.doc.styles:
                font = _style_font(style)
                name = style.dxf.name
                if name not in used:
                    continue
                if is_gost_font(name) or is_gost_font(font):
                    continue
                if is_explicit_non_gost(name) or is_explicit_non_gost(font) or name.lower() == "standard":
                    try:
                        style.dxf.font = GOST_FONT_FILE
                        details.append(f"Стилю «{name}» назначен шрифт «{GOST_FONT_FILE}».")
                        auto = True
                    except Exception as exc:
                        details.append(f"Не удалось сменить шрифт стиля «{name}»: {exc}")
            for entity, h, target, kind in roundable:
                if kind != "inline":
                    new_du = mm_to_du(target, ctx.to_mm)
                    _set_entity_height(entity, new_du)
                if entity.dxftype() == "MTEXT":
                    try:
                        entity.dxf.text = rewrite_mtext_heights(str(entity.dxf.text or ""), snap_drawing_height)
                    except Exception:
                        pass
                auto = True
            # размерный текст: DIMTXT стиля и переопределения
            try:
                for dimstyle in ctx.doc.dimstyles:
                    try:
                        dimtxt = float(dimstyle.dxf.dimtxt)
                    except Exception:
                        continue
                    h_mm = du_to_mm(dimtxt, ctx.to_mm)
                    if h_mm < 1 or h_mm > 45:
                        continue
                    target = snap_drawing_height(h_mm, allow_large=False)
                    if abs(target - h_mm) > 0.12:
                        dimstyle.dxf.dimtxt = mm_to_du(target, ctx.to_mm)
                        details.append(f"DIMTXT стиля «{dimstyle.dxf.name}»: {h_mm:.2f} → {target:.1f} мм")
                        auto = True
                for dim in list(ctx.doc.modelspace().query("DIMENSION")):
                    if dim.dxf.hasattr("dimtxt"):
                        dimtxt = float(dim.dxf.dimtxt)
                        h_mm = du_to_mm(dimtxt, ctx.to_mm)
                        target = snap_drawing_height(h_mm)
                        if abs(target - h_mm) > 0.12:
                            dim.dxf.dimtxt = mm_to_du(target, ctx.to_mm)
                            auto = True
            except Exception as exc:
                details.append(f"Размерный текст проверить не удалось: {exc}")
            if roundable:
                details.append(
                    f"Округлено высот текста: {len(roundable)}. "
                    f"Надписи на поле чертежа выше {DRAWING_TEXT_MAX_MM:.0f} мм приведены к {DRAWING_TEXT_MAX_MM:.0f} мм "
                    "(10–20 мм ряда ГОСТ обычно оставляют для титульных надписей)."
                )

        findings = len(bad_styles) + len(unknown_styles) + len(roundable)
        if findings == 0:
            return self.result("ok", "Шрифты и высоты текста соответствуют проверенным правилам ГОСТ 2.304.", details)
        severity = "warning"
        msg = "Есть замечания по шрифтам и/или высотам текста."
        if auto:
            msg = "Шрифты и высоты текста приведены к ряду ГОСТ 2.304 (имя шрифта ГОСТ type A)."
        return self.result(severity, msg, details, auto_fixed=auto, findings_count=findings)
